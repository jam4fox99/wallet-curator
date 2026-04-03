# Wallet Curator — Cloud Dashboard Specification

## IMPORTANT: READ BEFORE DOING ANYTHING

This spec describes a major new feature: a cloud-deployed PnL dashboard with automatic trade syncing from a VPS. Before writing any code:

1. **Create a new branch:** `git checkout -b cloud-dashboard` from the current main. Do NOT modify main directly.
2. **Read this ENTIRE spec first.** There are places where you must STOP and wait for user input (marked with 🛑 STOP).
3. **Go into plan mode first.** Produce a build plan, cross-check for gaps, ask questions if anything is unclear. Do NOT start coding until the user approves the plan.

---

## Overview

The wallet-curator project is getting a cloud-deployed web dashboard that automatically syncs trade data from a SharpAIO VPS, computes P&L hourly, and displays it in a Polymarket-style trading dashboard.

**Current state:** Local CLI tool with SQLite, manual CSV ingest, local P&L computation.

**Target state:** Cloud-hosted Dash (Plotly) web app on Railway with Postgres, automatic trade syncing from VPS every 5 minutes, hourly P&L recomputation + resolution checks, Polymarket-style P&L chart, daily breakdown tables, per-wallet drill-down.

---

## System Architecture

```
┌─────────────────────────────────────────────────┐
│                  VPS (Amsterdam)                  │
│                                                   │
│  SharpAIO (trading bot)                           │
│    └── writes to polymarket_copytrade.csv         │
│    └── config/active_wallets.csv                  │
│                                                   │
│  sync_script.py (standalone, runs 24/7)           │
│    └── reads CSV every 5 min                      │
│    └── pushes new trades to Railway Postgres      │
│    └── pushes active wallet changes hourly        │
│    └── pushes sync status metadata                │
│    └── auto-detects Sharp version folder          │
│    └── tracks last synced tx_hash locally         │
└──────────────────────┬────────────────────────────┘
                       │ Postgres connection
                       │ (every 5 min)
                       ▼
┌─────────────────────────────────────────────────┐
│              Railway (Cloud)                      │
│                                                   │
│  Postgres Database                                │
│    └── trades, positions, resolutions, etc.       │
│    └── pnl_history (hourly snapshots for chart)   │
│                                                   │
│  Dash Web App (always-on)                         │
│    └── Hourly pipeline: rebuild positions →       │
│        resolve tokens → compute P&L →             │
│        store pnl_history                          │
│    └── Serves dashboard at public URL             │
│    └── Basic auth (username/password)             │
│    └── Resolution checks via Gamma API            │
└─────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              User's Browser                       │
│                                                   │
│  Tab 1: Portfolio Overview                        │
│    └── P&L chart (1D/3D/7D/15D/30D/ALL)         │
│    └── Daily breakdown table (calendar picker)    │
│    └── Refresh P&L button                         │
│                                                   │
│  Tab 2: Per-Wallet Charts                         │
│    └── Searchable wallet dropdown                 │
│    └── Individual P&L chart + time selectors      │
└─────────────────────────────────────────────────┘
```

---

## What Gets Deprecated

Move these files to `lib/_archived/` (do NOT delete):

- `lib/memory.py` — Mem0 wrapper
- `lib/evaluator.py` — Claude API evaluation
- `lib/analyzer.py` — Weekly P&L buckets + profile builder for eval prompt

Remove from `curator.py`:
- `evaluate` command and its subparser
- `ingest-sim` command and its subparser (sharp sims no longer used)
- `repair` command and its subparser (wasn't working well)

Remove from `requirements.txt`:
- `mem0ai`

Keep these CLI commands working (but LOW PRIORITY — build everything else first):
- `ingest` — still works but connects to cloud Postgres instead of local SQLite
- `pnl` — still works but reads from cloud Postgres
- `status` — still works but reads from cloud Postgres

The CLI commands should read a `DATABASE_URL` env var. If set, connect to Postgres. If not set, fall back to local SQLite. This lets them work in both environments.

---

## Component 1: VPS Sync Script (sync_script.py)

**This is a STANDALONE single Python file.** It does NOT import from the lib/ folder. All normalization functions are copied directly into this file. The user will copy this one file to the VPS and run it.

### Dependencies (install on VPS)

```
pip install psycopg2-binary
```

That's it. No pandas, no Flask, no other dependencies. The script uses only stdlib + psycopg2.

### Configuration

The script reads from environment variables or a `.env` file on the VPS:

```env
# Database connection (Railway Postgres)
DATABASE_URL=postgresql://user:pass@host:port/dbname

# Sharp config
SHARP_DOWNLOADS_DIR=C:\Users\Administrator\Downloads
SHARP_CSV_FILENAME=polymarket_copytrade.csv
SHARP_CSV_SUBPATH=config\trade_result_csv
SHARP_WALLETS_FILENAME=active_wallets.csv  
SHARP_WALLETS_SUBPATH=config\polymarket_csv

# Sync settings
SYNC_INTERVAL_SECONDS=300
WALLET_SYNC_INTERVAL_SECONDS=3600
```

### Version Detection Logic

The script auto-detects which Sharp version folder is the current one:

```python
def find_current_sharp_folder(downloads_dir):
    """Find the highest-versioned Sharp folder in Downloads.
    
    Scans for FOLDERS (not .zip files) with version-number names.
    Version names like 3.8.5, 3.8.3.1, 3.8.2.22 — each dot-separated
    segment is compared numerically.
    
    3.8.5 > 3.8.3.1 > 3.8.2.22
    
    Only considers folders, not zip files.
    Returns the full path to the highest version folder.
    """
```

**Edge cases:**
- If the highest version folder doesn't contain the CSV (user downloaded new version but hasn't copied config yet): fall back to the next highest version that DOES have the CSV. Log a warning.
- If NO version folder has the CSV: log error, sleep, retry next cycle.
- The script logs which folder it's currently reading from on every cycle.

### CSV Reading with File Locking Safety

Sharp writes to the CSV continuously. The sync script reads it at the same time. To avoid reading a partially-written row:

```python
def read_csv_safely(filepath):
    """Read the CSV, handling the case where Sharp is mid-write.
    
    - Read the entire file as text
    - Split into lines
    - Validate the LAST line: check it has the correct number of commas
      (matching the header). If not, discard it — it's a partial write.
    - Return all valid lines.
    """
```

### Normalization (built into the script)

Copy these functions directly into sync_script.py (do NOT import from lib/):

```python
def normalize_wallet(addr):
    """Lowercase and strip wallet address."""
    return str(addr).strip().lower()

def normalize_token_id(token_id):
    """Normalize to 0x-prefixed lowercase hex.
    
    Handles: hex strings, decimal strings, scientific notation fallback.
    Scientific notation path logs a loud warning — means dtype enforcement
    failed somewhere upstream.
    """
    token_id = str(token_id).strip()
    if token_id.startswith('0x'):
        return token_id.lower()
    try:
        return hex(int(token_id)).lower()
    except ValueError:
        pass
    try:
        val = int(float(token_id))
        print(f"⚠️ WARNING: Token ID in scientific notation: {token_id} — precision loss likely")
        return hex(val).lower()
    except (ValueError, OverflowError):
        pass
    print(f"❌ ERROR: Unrecognizable token ID format: {token_id}")
    return token_id.lower()

def normalize_game(market_name):
    """Extract canonical game name from market name prefix.
    Returns: CS2, LOL, DOTA, VALO, or UNKNOWN.
    """
    if market_name.startswith('Counter-Strike:'):
        return 'CS2'
    elif market_name.startswith('LoL:'):
        return 'LOL'
    elif market_name.startswith('Dota 2:'):
        return 'DOTA'
    elif market_name.startswith('Valorant:'):
        return 'VALO'
    else:
        return 'UNKNOWN'

def parse_game_from_whitelist(whitelist):
    """Parse market_whitelist patterns into canonical game name.
    Multi-game wallets → ESPORTS.
    """
    if not whitelist or not str(whitelist).strip():
        return 'UNKNOWN'
    whitelist = str(whitelist).lower()
    games = set()
    if any(p in whitelist for p in ['cs2', 'csgo', 'counter-strike']):
        games.add('CS2')
    if 'lol' in whitelist:
        games.add('LOL')
    if any(p in whitelist for p in ['dota2', 'dota-2', 'dota']):
        games.add('DOTA')
    if any(p in whitelist for p in ['val', 'valorant']):
        games.add('VALO')
    if len(games) == 0:
        return 'UNKNOWN'
    elif len(games) == 1:
        return games.pop()
    else:
        return 'ESPORTS'
```

### Main Loop

```python
def main():
    """Main sync loop. Runs forever, every 5 minutes."""
    
    last_synced_tx_hash = load_last_synced_hash()  # from local file
    last_wallet_sync = 0  # epoch timestamp
    
    while True:
        try:
            # 1. Find current Sharp version folder
            sharp_folder = find_current_sharp_folder(DOWNLOADS_DIR)
            
            # 2. Read CSV safely
            csv_path = sharp_folder / SHARP_CSV_SUBPATH / SHARP_CSV_FILENAME
            if not csv_path.exists():
                # Fall back to previous version
                sharp_folder = find_fallback_folder(DOWNLOADS_DIR, sharp_folder)
                csv_path = sharp_folder / SHARP_CSV_SUBPATH / SHARP_CSV_FILENAME
            
            rows = read_csv_safely(csv_path)
            
            # 3. Parse rows, normalize, filter to Buy/Sell only
            valid_trades = []
            for row in rows:
                if row['Action'] not in ('Buy', 'Sell'):
                    continue
                trade = {
                    'tx_hash': row['Tx Hash'].strip(),
                    'timestamp': row['Date'].strip(),
                    'master_wallet': normalize_wallet(row['Master Wallet']),
                    'own_wallet': row['Own Wallet'].strip(),
                    'action': row['Action'],
                    'market': row['Market'].strip(),
                    'outcome': row['Outcome'].strip(),
                    'token_id': normalize_token_id(row['Token ID']),
                    'price': float(row['Price']),
                    'shares': float(row['Shares']),
                    'invested': float(row['Invested']),
                    'received': float(row['Received']),
                    'pnl_pct': parse_float_or_none(row['PNL %']),
                    'pct_sold': parse_float_or_none(row['% Sold']),
                    'reason': row.get('Reason', ''),
                    'game': normalize_game(row['Market']),
                }
                valid_trades.append(trade)
            
            # 4. Find new trades (after last synced hash)
            new_trades = find_trades_after_hash(valid_trades, last_synced_tx_hash)
            
            # 5. Push to Postgres (dedup on tx_hash via INSERT ON CONFLICT DO NOTHING)
            if new_trades:
                push_trades(new_trades)
                last_synced_tx_hash = new_trades[-1]['tx_hash']
                save_last_synced_hash(last_synced_tx_hash)
                print(f"✅ Synced {len(new_trades)} new trades from {sharp_folder.name}")
            
            # 6. Update sync status in Postgres
            update_sync_status(sharp_folder.name, len(new_trades))
            
            # 7. Sync active wallets (hourly)
            if time.time() - last_wallet_sync > WALLET_SYNC_INTERVAL:
                wallets_path = sharp_folder / SHARP_WALLETS_SUBPATH / SHARP_WALLETS_FILENAME
                if wallets_path.exists():
                    sync_active_wallets(wallets_path)
                    last_wallet_sync = time.time()
                    print(f"✅ Active wallets synced")
            
        except Exception as e:
            print(f"❌ Sync error: {e}")
            # Don't crash — sleep and retry
        
        time.sleep(SYNC_INTERVAL_SECONDS)
```

### Tracking Last Synced Position

The script stores the last synced `tx_hash` in a local file on the VPS: `last_sync.txt`.

On restart: read this file, find that tx_hash's position in the CSV, sync everything after it.

If the file doesn't exist (first run): sync the ENTIRE CSV. This is the backfill path.

If the tx_hash isn't found in the CSV (file was replaced): sync the entire CSV, dedup will prevent duplicates.

### Active Wallets Sync

Reads `active_wallets.csv` from the Sharp config folder. Pushes the current wallet list to a `synced_active_wallets` table in Postgres. The cloud app diffs this against its `last_known_wallets` table to detect changes.

**Parsing:** Read the CSV, skip the `__global__` row, extract `address` and `market_whitelist` columns, normalize wallet addresses to lowercase, parse game from whitelist patterns.

### Sync Status Metadata

After every cycle, UPDATE a `sync_status` table in Postgres:

```sql
CREATE TABLE sync_status (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_sync_at TIMESTAMP NOT NULL,
    current_version_folder TEXT NOT NULL,
    trades_synced_this_cycle INTEGER NOT NULL DEFAULT 0,
    total_trades_synced INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    CHECK (id = 1)  -- singleton row
);
```

The dashboard reads this to show "Currently syncing from: 3.8.5" and "Last sync: 2 min ago".

---

## Component 2: Postgres Schema

The cloud Postgres database uses a schema evolved from the SQLite schema, with additions for charting and sync.

### 🛑 STOP: Railway Setup Required

Before implementing the database, the user needs to:

1. Create a Railway account at https://railway.app
2. Create a new project
3. Add a Postgres database to the project
4. Get the DATABASE_URL connection string
5. Provide the DATABASE_URL to you

**Stop and ask the user for the DATABASE_URL before proceeding with any database code.**

### Tables

```sql
-- Raw trade log entries (synced from VPS)
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    tx_hash TEXT UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
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
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trades_wallet ON trades(master_wallet);
CREATE INDEX idx_trades_token ON trades(token_id);
CREATE INDEX idx_trades_timestamp ON trades(timestamp);
CREATE INDEX idx_trades_game ON trades(game);
CREATE INDEX idx_trades_tx_hash ON trades(tx_hash);

-- Computed positions (FULL REBUILD hourly from trades)
-- Only positions where total_shares_bought > 0 AND net_shares >= 0
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
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
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(master_wallet, token_id)
);

CREATE INDEX idx_positions_wallet ON positions(master_wallet);
CREATE INDEX idx_positions_token ON positions(token_id);

-- Token resolution cache
-- Rows created when new token_ids appear in positions.
-- market and outcome populated from trade data.
-- Resolver compares stored outcome against Gamma API winning outcome.
CREATE TABLE resolutions (
    token_id TEXT PRIMARY KEY,
    market TEXT,
    outcome TEXT,
    resolved INTEGER NOT NULL DEFAULT 0,  -- 0=unresolved, 1=won($1), -1=lost($0), -2=unresolvable
    resolution_price REAL,
    resolved_at TIMESTAMPTZ,              -- when the market actually resolved (from API or fallback)
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Wallet P&L summary (recomputed hourly, AFTER resolution checks)
CREATE TABLE wallet_pnl (
    master_wallet TEXT PRIMARY KEY,
    game TEXT,
    total_invested REAL NOT NULL DEFAULT 0,
    total_received_sells REAL NOT NULL DEFAULT 0,
    total_received_resolutions REAL NOT NULL DEFAULT 0,
    total_lost_resolutions REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_invested REAL NOT NULL DEFAULT 0,
    total_pnl REAL NOT NULL DEFAULT 0,     -- realized + unrealized mark-to-market
    unique_markets INTEGER NOT NULL DEFAULT 0,
    unique_tokens INTEGER NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    excluded_positions INTEGER NOT NULL DEFAULT 0,
    first_trade TIMESTAMPTZ,
    last_trade TIMESTAMPTZ,
    last_computed TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- P&L history for charting (one row per wallet per hour + one aggregate row)
-- This is what powers the Polymarket-style P&L chart
CREATE TABLE pnl_history (
    id SERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    master_wallet TEXT,          -- NULL = aggregate portfolio total
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    total_pnl REAL NOT NULL DEFAULT 0,
    total_invested REAL NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    unique_markets INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_pnl_history_time ON pnl_history(recorded_at);
CREATE INDEX idx_pnl_history_wallet ON pnl_history(master_wallet);
CREATE INDEX idx_pnl_history_aggregate ON pnl_history(recorded_at) WHERE master_wallet IS NULL;

-- Sync status from VPS (singleton row, updated every 5 min)
CREATE TABLE sync_status (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_sync_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_version_folder TEXT NOT NULL DEFAULT '',
    trades_synced_this_cycle INTEGER NOT NULL DEFAULT 0,
    total_trades_synced INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    CHECK (id = 1)
);

-- Active wallets (synced from VPS hourly)
CREATE TABLE synced_active_wallets (
    wallet_address TEXT PRIMARY KEY,
    market_whitelist TEXT,
    game_filter TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Last known wallet state (for change detection)
CREATE TABLE last_known_wallets (
    wallet_address TEXT PRIMARY KEY,
    game_filter TEXT,
    snapshot_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Wallet change ledger
CREATE TABLE wallet_changes (
    id SERIAL PRIMARY KEY,
    change_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    wallet_address TEXT NOT NULL,
    action TEXT NOT NULL,             -- 'ADDED' or 'REMOVED'
    game_filter TEXT,
    retirement_summary TEXT
);

CREATE INDEX idx_changes_wallet ON wallet_changes(wallet_address);
CREATE INDEX idx_changes_date ON wallet_changes(change_date);

-- Hidden wallets (user preference, persisted)
CREATE TABLE hidden_wallets (
    wallet_address TEXT PRIMARY KEY,
    hidden_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Sim data (kept for reference, low priority)
CREATE TABLE sim_registry (
    sim_number SERIAL PRIMARY KEY,
    original_filename TEXT NOT NULL,
    renamed_filename TEXT NOT NULL,
    sim_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    wallet_count INTEGER
);

CREATE TABLE sim_snapshots (
    id SERIAL PRIMARY KEY,
    sim_number INTEGER NOT NULL REFERENCES sim_registry(sim_number),
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
    UNIQUE(sim_number, wallet_address)
);

CREATE TABLE sim_profiles (
    id SERIAL PRIMARY KEY,
    sim_number INTEGER NOT NULL REFERENCES sim_registry(sim_number),
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
    UNIQUE(sim_number, wallet_address)
);

-- Pipeline run log (tracks when each hourly pipeline ran)
CREATE TABLE pipeline_log (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    positions_rebuilt INTEGER,
    tokens_resolved INTEGER,
    pnl_computed INTEGER,
    history_recorded INTEGER,
    error TEXT
);
```

---

## Component 3: Hourly Pipeline

Runs every hour on Railway (scheduled via APScheduler in the Dash app, or a background thread with a timer).

### Pipeline Steps (in this exact order)

```
Step 1: Rebuild positions
    - TRUNCATE positions (Postgres equivalent of DROP + recreate)
    - INSERT INTO positions ... SELECT ... FROM trades GROUP BY master_wallet, token_id
    - HAVING total_shares_bought > 0 AND net_shares >= 0
    - Excluded positions (sells without buys, or more sells than buys) are NOT stored
    - Count excluded per wallet for the excluded_positions metric

Step 2: Ensure resolution entries
    - INSERT INTO resolutions (token_id, market, outcome) 
      SELECT DISTINCT token_id, market, outcome FROM positions 
      WHERE token_id NOT IN (SELECT token_id FROM resolutions)
      ON CONFLICT DO NOTHING

Step 3: Resolve tokens via Gamma API
    - Query unresolved tokens: resolved = 0 AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL '1 hour')
    - Convert token IDs to decimal for API queries: str(int(hex_token, 16))
    - Batch in groups of 50: GET https://gamma-api.polymarket.com/markets?clob_token_ids=D1&clob_token_ids=D2&...
    - Parse response: find token in clobTokenIds array → outcomePrices[i]: "1" = won, "0" = lost
    - If market.closed = true: resolved = 1 or -1, store resolved_at from API response
    - If market.closed = false: leave resolved = 0, update checked_at
    - If API doesn't return resolution timestamp: resolved_at = last buy timestamp for that token + 24 hours
    - If token not found in API response: try CLOB API fallback. If both fail: resolved = -2
    - 50ms delay between batches. Progress logging.
    - Handle API unreachable gracefully: log warning, skip, continue

Step 4: Compute P&L
    - For each wallet, for each position joined with resolutions:
      - avg_cost_basis = total_invested / total_shares_bought
      - Sell P&L: total_received - (avg_cost_basis * total_shares_sold)
      - If resolved = 1 (won): (net_shares * 1.0) - (avg_cost_basis * net_shares)
      - If resolved = -1 (lost): 0 - (avg_cost_basis * net_shares)
      - If resolved = 0 or -2: unrealized = avg_cost_basis * net_shares
      - total_pnl = realized + unrealized mark-to-market
    - Guards: skip if total_shares_bought == 0, skip if net_shares < 0 (shouldn't exist in positions but just in case)
    - Aggregate per wallet, INSERT/UPDATE wallet_pnl

Step 5: Record P&L history
    - For each wallet: INSERT INTO pnl_history (recorded_at, master_wallet, realized_pnl, unrealized_pnl, total_pnl, ...)
    - For aggregate: INSERT INTO pnl_history (recorded_at, master_wallet=NULL, realized_pnl=SUM(...), ...)
    - This is what powers the chart

Step 6: Detect wallet changes
    - Read synced_active_wallets table (pushed by VPS sync script)
    - Diff against last_known_wallets table
    - Log changes to wallet_changes table
    - Generate retirement summaries for removed wallets
    - Overwrite last_known_wallets with current state

Step 7: Log pipeline run
    - INSERT into pipeline_log with stats
```

### Backfill on First Deploy

When the system first starts and pnl_history is empty but trades exist (from the sync script pushing historical data):

1. Compute what the P&L state SHOULD have been at each day boundary
2. For each day from the earliest trade to today:
   - Build positions from trades up to that day's end
   - Use resolution data (with resolved_at timestamps) to determine which tokens were resolved by that day
   - If resolved_at is unknown: use the fallback (last buy + 24 hours)
   - Compute P&L as of that day
   - Insert into pnl_history with that day's timestamp (one entry per day for backfill, not hourly)
3. After backfill, switch to normal hourly recording

This gives the chart historical data from day one of trading.

### Resolution Timestamp Fallback

When the Gamma API returns that a market is resolved but doesn't include a resolution timestamp:

```python
# Fallback: last buy transaction for this token (across ALL wallets) + 24 hours
SELECT MAX(timestamp) FROM trades WHERE token_id = ? AND action = 'Buy'
# Add 24 hours to that timestamp → use as resolved_at
```

This is a proxy — the actual resolution likely happened sometime between the last trade and when we checked. 24 hours is a reasonable estimate for esports markets which typically resolve within hours of the match ending.

---

## Component 4: Dash Web App

### 🛑 STOP: Railway App Setup Required

Before deploying the app, the user needs to:

1. Add a new service to the Railway project (from the same project as the Postgres DB)
2. Connect it to the git repo (cloud-dashboard branch)
3. Set environment variables in Railway:
   - `DATABASE_URL` — auto-populated if Postgres is in the same project
   - `DASH_USERNAME` — basic auth username
   - `DASH_PASSWORD` — basic auth password
4. Confirm the deploy is working

**Stop and walk the user through this setup before proceeding.**

### Stack

- **Dash** (by Plotly) — Python-native web framework
- **dash-bootstrap-components** — dark theme, layout components
- **dash_table.DataTable** — sortable, filterable tables with conditional formatting
- **Plotly graph objects** — P&L line charts
- **APScheduler** — runs the hourly pipeline as a background job

### Dependencies (add to requirements.txt)

```
dash
dash-bootstrap-components
plotly
pandas
psycopg2-binary
apscheduler
requests
openpyxl
```

### App Entry Point (app.py)

```python
# Basic structure
import dash
from dash import html, dcc, dash_table, Input, Output, State, callback
import dash_bootstrap_components as dbc
from apscheduler.schedulers.background import BackgroundScheduler

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])
server = app.server  # for Railway deployment

# Basic auth
auth = dash_auth.BasicAuth(app, {
    os.environ['DASH_USERNAME']: os.environ['DASH_PASSWORD']
})

# Background scheduler for hourly pipeline
scheduler = BackgroundScheduler()
scheduler.add_job(run_hourly_pipeline, 'interval', hours=1)
scheduler.start()

# Also run midnight UTC daily snapshot
scheduler.add_job(run_daily_snapshot, 'cron', hour=0, minute=0)
```

### Layout — Two Tabs

**Tab 1: Portfolio Overview**

```
┌─────────────────────────────────────────────────────────────────┐
│  Wallet Curator Dashboard                                       │
│                                                                 │
│  Syncing from: 3.8.5  |  Last sync: 2 min ago  |  Trades: 10.9K│
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                                                             ││
│  │  📈 Portfolio P&L                    [1D][3D][7D][15D][30D][ALL]│
│  │  $73,023.66                                                 ││
│  │  All-Time                                                   ││
│  │                                                             ││
│  │  ╭─────────────────────────────────────╮                   ││
│  │  │          P&L line chart              │                   ││
│  │  │     (aggregate total_pnl over time)  │                   ││
│  │  ╰─────────────────────────────────────╯                   ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  [Refresh P&L 🔄]                                               │
│                                                                 │
│  Daily Breakdown   📅 [Mar 18] to [Mar 21]                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ Wallet  | Filter | Actual | Sim# | Invested | Realized |   ││
│  │         |        |        |      |          | P&L      |   ││
│  │         |        |        |      |          |          |   ││
│  │ 0x82d2  | CS2    | CS2    | #1   | $211     | +$188    |...││
│  │ 0x8d9f  | LOL    | LOL    | #1   | $317     | +$83     |...││
│  │ ...     |        |        |      |          |          |   ││
│  │─────────|────────|────────|──────|──────────|──────────|───││
│  │ Totals (excl. 3 hidden)   |      | $5,240   | +$1,862  |...││
│  │ (True total incl. hidden: $5,890 | +$2,104)             |  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  [👁 Show Hidden Wallets]                                       │
│                                                                 │
│  Recent Changes                                                 │
│  ✅ ADDED 0x82d2e4 (CS2) — Mar 21                              │
│  🔴 REMOVED 0xb624f2 (VALO) — Mar 21                          │
└─────────────────────────────────────────────────────────────────┘
```

**Tab 2: Per-Wallet Charts**

```
┌─────────────────────────────────────────────────────────────────┐
│  Per-Wallet Analysis                                            │
│                                                                 │
│  Select wallet: [🔍 0x82d2e4dbb0a849ff8...  ▼]                │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                                                             ││
│  │  📈 0x82d2e4 (CS2) P&L           [1D][3D][7D][15D][30D][ALL]│
│  │  +$1,862.42                                                 ││
│  │                                                             ││
│  │  ╭─────────────────────────────────────╮                   ││
│  │  │     Individual wallet P&L chart      │                   ││
│  │  ╰─────────────────────────────────────╯                   ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  Wallet Stats                                                   │
│  Invested: $5,240 | Realized: +$1,862 | Unrealized: $340      │
│  Markets: 97 | Trades: 3,775 | Active since: Feb 10            │
│  Excluded positions: 0                                          │
└─────────────────────────────────────────────────────────────────┘
```

### Dashboard Styling

```python
# Dark theme colors
COLORS = {
    'background': '#0f0f0f',
    'card': '#1a1a1a',
    'text': '#e5e5e5',
    'text_secondary': '#9ca3af',
    'positive': '#22c55e',     # green
    'negative': '#ef4444',     # red
    'border': '#2a2a2a',
    'button': '#2563eb',
    'button_hover': '#1d4ed8',
}

# Number font: JetBrains Mono or system monospace
# All P&L values: green if positive, red if negative
# Wallet addresses truncated: 0x82d2e4...beea
```

### P&L Chart Details

- Plotly line chart with dark theme
- X-axis: time, Y-axis: total P&L in dollars
- Time selectors: 1D, 3D, 7D, 15D, 30D, ALL — filter the chart data range
- Hover shows exact value + timestamp
- The big number above the chart ($73,023.66) is the CURRENT total P&L
- Below it: "All-Time" or "Last 7 Days" etc. depending on selector
- Chart shows the P&L LINE — plotting total_pnl from pnl_history over time

### Daily Breakdown Table

- Uses dash_table.DataTable
- Calendar date picker (dcc.DatePickerRange) for contiguous date ranges
- Columns: Wallet | Filter | Actual | Sim # | Invested | Realized P&L | Unrealized | Total P&L | Markets | Trades | In CSV | [Hide]
- **P&L attribution is Option A:** P&L goes on the EXIT day (sell or resolution date). Cost basis pulled from full trade history regardless of date window.
- Conditional formatting: green text for positive P&L, red for negative
- Sortable by clicking column headers
- Default sort: Total P&L descending
- Totals row at bottom (excludes hidden wallets, shows note about hidden)
- The [Hide] column has an eye icon button per row

### Hidden Wallets

- Stored in `hidden_wallets` table (wallet_address + hidden_at)
- Default view: hidden wallets excluded from table and table totals
- "Show Hidden Wallets" toggle: when on, hidden wallets appear with grayed-out/striped styling and an "unhide" button
- Hiding/unhiding is global — applies across all date views
- **The portfolio P&L CHART at the top always includes ALL wallets (including hidden).** The chart shows true portfolio value. Only the TABLE excludes hidden wallets.
- The totals row shows: "Totals (excluding N hidden wallets)" and "(True total incl. hidden: $X)"

### Date-Scoped P&L Computation

When the user selects a date range (e.g., March 18-20) in the daily table:

```
For each wallet:
  1. Get all SELL trades within March 18-20 for this wallet
  2. Get all resolved positions where resolved_at falls within March 18-20
  3. For sells: sell_pnl = received - (avg_cost_basis * shares_sold)
     - avg_cost_basis comes from the full position history (may include buys outside the window)
  4. For resolutions: resolved_pnl = (net_shares * resolution_price) - (avg_cost_basis * net_shares)
  5. Unrealized: positions with buys in the window but not yet resolved as of NOW
  6. Sum into: invested (buys in window), realized (sells + resolutions in window), unrealized, total
```

**Key:** Buys that happened BEFORE the window but sold/resolved WITHIN the window show their P&L in the window. The daily view answers "what exits happened on these days and how did they do."

### Refresh P&L Button

Triggers the full hourly pipeline immediately (positions rebuild → resolve → P&L → history). Returns when done. During processing, show a spinner/loading state. After completion, refresh all dashboard data.

---

## Component 5: Deployment

### 🛑 STOP: Deployment Setup Required

Walk the user through these steps one at a time:

1. **Railway account:** Create at https://railway.app if not already done
2. **New project:** Create a new Railway project
3. **Add Postgres:** Click "New" → "Database" → "PostgreSQL". Copy the DATABASE_URL.
4. **Add web service:** Click "New" → "GitHub Repo" → select the wallet-curator repo → select the `cloud-dashboard` branch
5. **Set env vars on the web service:**
   - `DATABASE_URL` — paste the Postgres connection string (may auto-populate)
   - `DASH_USERNAME` — whatever username you want
   - `DASH_PASSWORD` — whatever password you want
6. **Set build command:** `pip install -r requirements.txt`
7. **Set start command:** `python app.py`
8. **Deploy and verify** — check the public URL loads the dashboard

**Then for the VPS sync script:**

1. On the VPS, install Python 3.10+ if not already installed
2. Install psycopg2-binary: `pip install psycopg2-binary`
3. Copy `sync_script.py` to the VPS (can be anywhere, e.g., `C:\Users\Administrator\sync_script\`)
4. Create a `.env` file next to it with the DATABASE_URL and Sharp paths
5. Run: `python sync_script.py`
6. Verify trades are appearing in the dashboard

**Stop at each step and confirm with the user before proceeding.**

### Railway Configuration

**Procfile or railway.toml:**
```
[build]
builder = "nixpacks"

[deploy]
startCommand = "python app.py"
healthcheckPath = "/"
healthcheckTimeout = 30
```

**The app should bind to the port Railway provides:**
```python
port = int(os.environ.get('PORT', 8050))
app.run(host='0.0.0.0', port=port, debug=False)
```

---

## Component 6: Data Normalization

All normalization happens in TWO places — they must be consistent:

1. **sync_script.py** (standalone, runs on VPS) — normalization functions copied directly into the file
2. **lib/normalizers.py** (in the main project, used by the Dash app) — canonical normalization functions

**These MUST produce identical results.** If you change one, change the other.

### All wallet addresses: lowercase everywhere
### All token IDs: 0x-prefixed lowercase hex everywhere
### All games: CS2, LOL, DOTA, VALO, ESPORTS, UNKNOWN
### All timestamps: UTC

**Timezone edge case:** The VPS is in Amsterdam but Sharp may log trades in local VPS time or UTC. Check the timestamp format in the CSV — if it includes timezone info (Z suffix or +00:00), parse accordingly. If it's naive (no timezone), assume UTC. Log a warning on first run showing the timestamp format detected so the user can verify.

### Token ID for Gamma API

The Gamma API requires DECIMAL token IDs, not hex. Convert with:
```python
def token_id_to_decimal(hex_token):
    return str(int(hex_token, 16))
```

Round-trip must work: `token_id_to_decimal(normalize_token_id(decimal_str)) == decimal_str`

---

## Component 7: P&L Computation

### P&L Formula (same as local version)

```
Per position (one wallet + one token):

  Guards:
    If total_shares_bought == 0: skip (shouldn't be in positions table)
    If net_shares < 0: skip (shouldn't be in positions table)
    avg_cost_basis = total_invested / total_shares_bought

  If token resolved = 1 (won):
    resolution_value = net_shares * 1.00
    resolved_pnl = resolution_value - (avg_cost_basis * net_shares)
  
  If token resolved = -1 (lost):
    resolved_pnl = 0 - (avg_cost_basis * net_shares)
  
  If token resolved = 0 or -2 (unresolved):
    unrealized_invested = avg_cost_basis * net_shares
  
  Sell P&L (regardless of resolution):
    sell_pnl = total_received - (avg_cost_basis * total_shares_sold)
  
  Total P&L = realized (sells + resolutions) + unrealized mark-to-market
```

### Positions Rebuild

```sql
TRUNCATE positions;

INSERT INTO positions (master_wallet, token_id, market, outcome, game,
    total_shares_bought, total_invested, total_shares_sold, total_received,
    net_shares, avg_cost_basis)
SELECT 
    master_wallet, token_id,
    MAX(market), MAX(outcome), MAX(game),
    SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END),
    SUM(CASE WHEN action='Buy' THEN invested ELSE 0 END),
    SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END),
    SUM(CASE WHEN action='Sell' THEN received ELSE 0 END),
    SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END),
    CASE WHEN SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) > 0
         THEN SUM(CASE WHEN action='Buy' THEN invested ELSE 0 END) / SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
         ELSE NULL END
FROM trades
GROUP BY master_wallet, token_id
HAVING SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) > 0
   AND (SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END)) >= 0;
```

### Excluded Positions Count

```sql
-- Count positions excluded per wallet (for the excluded_positions metric)
SELECT master_wallet, COUNT(DISTINCT token_id) as excluded
FROM trades
WHERE (master_wallet, token_id) NOT IN (SELECT master_wallet, token_id FROM positions)
GROUP BY master_wallet;
```

---

## Error Handling

- **Gamma API unreachable:** Log warning, skip resolution step, continue pipeline. Dashboard shows "⚠️ Resolution API unreachable" in status bar.
- **Postgres unreachable (from sync script):** Log error, sleep, retry next cycle. Don't crash.
- **Postgres unreachable (from Dash app):** Show "Database unavailable" error page.
- **Sync script crashes:** Manual restart by user. The last_sync.txt file preserves state so no data is lost.
- **Hourly pipeline fails midway:** Log to pipeline_log with error. Don't corrupt partially-computed data — use transactions where possible.
- **Malformed CSV rows on VPS:** Skip invalid rows (wrong column count, Action not Buy/Sell). Log count. Continue.
- **Scientific notation token IDs:** Log loud warning. Means something is wrong with CSV parsing.
- **Unknown game names:** Normalize to UNKNOWN, log warning.
- **Division by zero in P&L:** Skip position, log warning (shouldn't happen if HAVING clause works).

---

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `sync_script.py` | Standalone VPS sync script (single file, no lib imports) |
| `app.py` | Dash web app entry point |
| `lib/cloud_db.py` | Postgres connection + queries (replaces SQLite-specific code) |
| `lib/pipeline.py` | Hourly pipeline orchestration |
| `lib/charts.py` | Chart data computation (aggregation, date filtering) |
| `lib/daily_pnl.py` | Date-scoped P&L computation for the daily table |
| `lib/backfill.py` | Historical pnl_history backfill logic |
| `Procfile` or `railway.toml` | Railway deployment config |
| `.env.example` | Example env vars for both VPS and Railway |

### Modified Files

| File | Changes |
|------|---------|
| `lib/db.py` | Add Postgres support (read DATABASE_URL, fall back to SQLite) |
| `lib/resolver.py` | Works with Postgres connection instead of SQLite |
| `lib/pnl.py` | Works with Postgres, add pnl_history recording |
| `lib/normalizers.py` | No changes needed (already correct) |
| `lib/changelog.py` | Remove Mem0 references, use snapshot-based diff with last_known_wallets |
| `curator.py` | Remove evaluate/ingest-sim/repair commands, add DATABASE_URL support for remaining CLI commands |
| `requirements.txt` | Remove mem0ai, add dash, dash-bootstrap-components, plotly, psycopg2-binary, apscheduler |

### Archived Files (move to lib/_archived/)

| File | Reason |
|------|--------|
| `lib/memory.py` | Mem0 deprecated |
| `lib/evaluator.py` | Evaluate command deprecated |
| `lib/analyzer.py` | Only used by evaluator |
| `lib/ingest_sim.py` | Sim ingest deprecated |
| `lib/repair.py` | Repair command deprecated |

---

## Build Order

1. **Branch + Postgres schema** — Create branch, set up database tables
   - 🛑 STOP: Need Railway account + Postgres DATABASE_URL from user
2. **lib/cloud_db.py** — Postgres connection management
3. **sync_script.py** — Standalone VPS sync script with all normalization built in
   - 🛑 STOP: Need user to set up VPS Python environment and run the script
4. **lib/pipeline.py** — Hourly pipeline (positions → resolve → P&L → history)
5. **lib/resolver.py updates** — Postgres-compatible, batch resolution with decimal token IDs
6. **lib/pnl.py updates** — Postgres-compatible P&L computation + pnl_history recording
7. **lib/backfill.py** — Historical chart data backfill
8. **app.py + dashboard layout** — Tab 1: Portfolio chart + daily table. Tab 2: Per-wallet charts.
9. **lib/daily_pnl.py** — Date-scoped P&L for the daily breakdown table
10. **lib/charts.py** — Chart data queries (time selectors, per-wallet)
11. **Hidden wallets** — Toggle, persist, table/totals exclusion
12. **Changelog** — Wallet change detection, display in dashboard
13. **Deployment** — Railway config, env vars, verify
    - 🛑 STOP: Walk user through Railway deployment setup
14. **Deprecation cleanup** — Archive old files, remove unused commands
15. **CLI updates (LOW PRIORITY)** — Make remaining CLI commands work with Postgres via DATABASE_URL

---

## Verification Plan

After each major step:

1. **After sync_script.py:** Trades appearing in Postgres? `SELECT COUNT(*) FROM trades` growing every 5 min?
2. **After pipeline:** Positions rebuilt? Resolutions checked? wallet_pnl populated? pnl_history has rows?
3. **After backfill:** pnl_history has historical data points going back to earliest trades?
4. **After dashboard Tab 1:** Chart renders with time selectors? Daily table shows date-scoped P&L? Sorting works? Green/red coloring?
5. **After dashboard Tab 2:** Searchable dropdown works? Individual wallet chart renders? Stats display correctly?
6. **After hidden wallets:** Hide persists across page loads? Table excludes hidden? Chart still includes all? Totals note hidden count?
7. **After deployment:** Public URL loads? Auth works? Sync script on VPS pushing data? Hourly pipeline running?

### Data Integrity Checks

```sql
-- All wallet addresses lowercase
SELECT master_wallet FROM trades WHERE master_wallet != LOWER(master_wallet);  -- should be 0

-- All token IDs are proper hex
SELECT token_id FROM trades WHERE LEFT(token_id, 2) != '0x';  -- should be 0

-- No positions with negative net_shares
SELECT * FROM positions WHERE net_shares < 0;  -- should be 0

-- No positions without buys
SELECT * FROM positions WHERE total_shares_bought = 0;  -- should be 0

-- pnl_history has data
SELECT COUNT(*), MIN(recorded_at), MAX(recorded_at) FROM pnl_history;

-- Resolution round-trip (spot check)
SELECT token_id, outcome, resolved, resolved_at FROM resolutions LIMIT 5;
```

---

## Key Design Decisions

1. Python does math, LLM does judgment (future). Pre-compute everything.
2. Postgres is the single source of truth in production. SQLite for local dev only.
3. Dedup on tx_hash. Overlapping syncs are safe.
4. Resolution caching is permanent. Unresolvable tokens (-2) skipped.
5. All wallet addresses lowercase. All token IDs hex. All games canonical. All timestamps UTC.
6. Sync script is standalone — no repo imports. Normalization duplicated for independence.
7. Hourly pipeline order is sacred: positions → resolution entries → resolve → P&L → history.
8. P&L chart shows ALL wallets including hidden. Table excludes hidden with noted totals.
9. Daily P&L uses Option A attribution: P&L on exit day, cost basis from full history.
10. Resolution timestamp fallback: last buy for token + 24 hours.
11. Positions table only stores valid positions (buys > 0, net_shares >= 0).
12. pnl_history stores per-wallet AND aggregate hourly data points for charting.
13. Backfill computes historical daily P&L states for chart history.
14. VPS sync script auto-detects Sharp version folder, falls back gracefully.
15. File locking safety: discard incomplete last line from CSV reads.
16. Per-wallet chart on separate tab with searchable dropdown and own time selectors.
17. Active wallets synced hourly from VPS, change detection via snapshot diff.
18. Basic auth via env vars. Public URL.
19. Deprecated features archived, not deleted.
20. CLI commands work with Postgres when DATABASE_URL is set (low priority).