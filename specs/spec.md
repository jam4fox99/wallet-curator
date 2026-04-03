# Wallet Curator Agent — Project Specification

## Overview

A local Python CLI tool that helps curate a Polymarket esports copy-trading wallet list. It ingests real trade logs from SharpAIO, tracks wallet performance via a SQLite database, resolves market outcomes via the Polymarket API, and uses an LLM (Claude API) + persistent memory (Mem0) to make intelligent add/remove/keep recommendations for the wallet CSV.

The user runs this tool manually — there is no cron, no VPS integration, no live automation. It's a decision-support tool that learns over time.

It also functions as a **standalone P&L tracking system** — the user can ingest data and view running P&L for all wallets at any time, completely independent of the evaluation/recommendation engine. The `ingest`, `ingest-sim`, `pnl`, and `status` commands require NO API keys and NO Mem0 setup.

---

## Architecture

Three layers:

1. **SQLite** — Source of truth for all transactional data. Trades, positions, token resolutions, computed P&L, sharp sim snapshots, active wallet change history. Deterministic, fast, queryable. **Required for everything.**
2. **Mem0** — Semantic pattern memory. Stores the LLM's qualitative observations about wallets, cross-wallet patterns, decision logs, and retired wallet profiles. Persists between runs so the agent builds institutional knowledge over time. **Only required for `evaluate` command.**
3. **Claude API** — Reasoning layer. Takes structured wallet profiles (from Python/SQLite) + pattern context (from Mem0) + the wallet criteria document and makes judgment calls about which wallets to add, remove, or keep. **Only required for `evaluate` command.**

The Python analysis engine does ALL number crunching. The LLM does NOT parse xlsx files or compute P&L — it receives pre-computed structured data and applies judgment.

---

## Data Normalization

### Game Name Normalization

Games appear in three different formats across the system:
- Sharp sim `Detail` column: `Counter-Strike`, `League of Legends`, `Dota 2`, `Valorant`
- Active wallets `market_whitelist`: `cs2-|csgo|counter-strike`, `lol-`, `dota2-|dota-2|dota`, `val-|valorant`
- Sharp log `Market` names: `LoL: Gen.G vs Dplus KIA`, `Counter-Strike: FURIA vs PARIVISION`

**All game references must be normalized to one of six canonical names:**

| Canonical | Sharp Sim Detail | market_whitelist patterns | Sharp Log Market prefix |
|-----------|-----------------|--------------------------|------------------------|
| `CS2` | Counter-Strike | cs2-, csgo, counter-strike | Counter-Strike: |
| `LOL` | League of Legends | lol- | LoL: |
| `DOTA` | Dota 2 | dota2-, dota-2, dota | Dota 2: |
| `VALO` | Valorant | val-, valorant | Valorant: |
| `ESPORTS` | (multiple) | contains patterns from 2+ games | N/A |
| `UNKNOWN` | (fallback) | no recognized pattern | no recognized prefix |

**Multi-game wallets:** If a wallet's `market_whitelist` contains patterns for 2 or more games, its game is `ESPORTS`.

**Unknown games:** If a market name, sim detail, or whitelist pattern doesn't match any recognized game, normalize to `UNKNOWN`. Do not crash. Log a warning so the user can investigate and the mapping can be extended.

Build a `normalize_game()` function used everywhere. Every table that stores a game value should store the canonical form.

### Wallet Address Normalization

**All wallet addresses are normalized to lowercase everywhere.** This applies to:
- `master_wallet` from Sharp logs
- `address` from active_wallets.csv
- `wallet_address` from sharp sims
- Any wallet address used in Mem0 queries or storage

### Token ID Normalization

**CRITICAL:** Token IDs appear in two different formats across the system:
- Sharp logs: hex format, e.g. `0x166537c9b0d254460a38ca4830f631e7b5d06054a4c24094f5aae204a1c6f0e7`
- Sharp sim DRL sheets: decimal format, e.g. `68488748673468105962081122103528952065833191669726581193811815055243895971119`

These are the SAME token — just hex vs decimal representations of the same number. **All token IDs must be normalized to a consistent format on ingest.** Use hex format (with `0x` prefix, lowercase) as the canonical internal form. When ingesting sim DRL data, convert decimal token IDs to hex. Build a `normalize_token_id()` function:

**NOTE:** The Gamma API expects DECIMAL format token IDs, not hex. Use `token_id_to_decimal()` to convert when querying the API:

```python
def token_id_to_decimal(hex_token: str) -> str:
    """Convert canonical hex token ID to decimal string for Gamma API queries.
    Round-trip verified: token_id_to_decimal(normalize_token_id(decimal)) == original_decimal
    """
    return str(int(hex_token, 16))
```

Build a `normalize_token_id()` function:

```python
def normalize_token_id(token_id: str) -> str:
    """Normalize token ID to lowercase hex with 0x prefix.
    
    Handles: hex strings (0x...), pure decimal strings, and 
    scientific notation from pandas float coercion (6.84e+76).
    """
    token_id = str(token_id).strip()
    
    # Already hex
    if token_id.startswith('0x'):
        return token_id.lower()
    
    # Try pure integer string first (the expected case when dtype=str works)
    try:
        return hex(int(token_id)).lower()
    except ValueError:
        pass
    
    # Fallback: scientific notation from float coercion (e.g. "6.848874867346811e+76")
    # WARNING: This path means dtype=str failed or wasn't applied.
    # float64 only has ~15 digits of precision — the resulting hex WILL BE WRONG
    # for token IDs longer than ~15 digits. Log a warning but try anyway.
    try:
        val = int(float(token_id))
        # Log warning: "Token ID {token_id} was in scientific notation — 
        # precision loss likely. Verify dtype=str is being enforced."
        return hex(val).lower()
    except (ValueError, OverflowError):
        pass
    
    # Unrecognizable format — return as-is lowercased, log error
    return token_id.lower()
```

**NOTE:** The scientific notation fallback is a safety net, NOT a reliable conversion path. If it triggers, it means the `dtype=str` enforcement isn't working and the resulting token IDs will be wrong. The code should log a prominent warning when this path is hit so the developer can fix the root cause. The `dtype=str` fix in the Pandas Dtype Safety section is the real solution — this fallback just prevents silent garbage.

Without token normalization, cross-referencing between Sharp logs and sim data will silently fail, and the resolution cache won't match tokens across data sources.

### Pandas Dtype Safety for Large IDs

**CRITICAL:** Token IDs and transaction hashes are 60-80+ character strings. If pandas reads them as numeric (float64), precision loss silently mangles them — only ~15 significant digits survive, the rest become zeros. The resulting hex conversion produces the WRONG token ID and cross-references silently fail.

**Rule: ALL ID-like columns must be forced to `str` dtype when reading with pandas.** This applies to:

For Sharp log CSVs:
```python
df = pd.read_csv(file, dtype={'Token ID': str, 'Tx Hash': str, 'Master Wallet': str}, on_bad_lines='skip')
```

For Sharp sim DRL xlsx sheets:
```python
df = pd.read_excel(file, sheet_name=sheet, dtype={'Token ID': str, 'Market ID': str, 'Source Trade ID': str})
```

Never let pandas infer dtypes for these columns. This is a silent data corruption bug that produces no errors — it just gives wrong results. If the `normalize_token_id()` function hits its scientific notation fallback path, that's a signal this enforcement isn't working.

---

## File Management

### Drop-and-Ingest Pattern

The user drops files into designated folders. When they run `ingest` (no args), the tool auto-detects unprocessed files, processes them, and renames them so they're archived in place. No files are deleted — everything becomes a chronological archive.

### File Extension Filtering

**Sharp logs folder (`data/sharp_logs/`):** Only process files ending in `.csv`. Ignore all other files (`.DS_Store`, `.txt`, `.xlsx`, hidden files, etc.).

**Sharp sims folder (`data/sims/`):** Only process files ending in `.xlsx`. Ignore all other files.

This prevents crashes from stray OS files (`.DS_Store` on Mac, `Thumbs.db` on Windows) or accidentally dropped files of the wrong type.

### Sharp Logs (`data/sharp_logs/`)

The user downloads `Polymarket_Copytrade.csv` from the VPS and drops it into this folder.

When `python curator.py ingest` runs:
1. Scans for `.csv` files that don't match the `ingested_` or `processed_` prefix pattern (i.e., new/unprocessed)
2. Reads the CSV, **forcing `str` dtype on Token ID, Tx Hash, and Master Wallet columns**, diffs against SQLite using `tx_hash` to find only new trades
3. **If there are new trades:** saves them to a dated archive file: `ingested_001_2026-03-20.csv` (sequential number + date)
4. **If there are NO new trades (all duplicates):** skips archive creation, prints "No new trades found in {filename} (all X trades already in database)."
5. Renames the original dropped file with `processed_` prefix + timestamp to avoid collisions: `processed_Polymarket_Copytrade_2026-03-20_143022.csv` (date + HHMMSS)
6. Ingests the new trades into SQLite
7. **In this exact order (ordering matters):**
   a. Full rebuild of `positions` table (DROP + recreate from all trades)
   b. Ensure all unique token_ids from `positions` have a row in `resolutions` (see Resolution Entry Creation below)
   c. Check all unresolved tokens (resolved = 0) against the Polymarket Gamma API
   d. Recompute wallet-level P&L (MUST happen AFTER resolution checks — newly resolved tokens need to be counted as realized, not unrealized)
8. Diffs `active_wallets.csv` against last known state and logs any changes
9. If any wallets were REMOVED: generates retirement summaries, stores in Mem0 if available, always logs to changelog

Prints summary: "Processed X file(s). Ingested Y new trades (Z already existed). W tokens newly resolved."

So the folder over time looks like:
```
data/sharp_logs/
├── ingested_001_2026-03-20.csv                    ← 4,200 new trades from first drop
├── processed_Polymarket_Copytrade_2026-03-20_143022.csv  ← original, renamed with timestamp
├── ingested_002_2026-03-22.csv                    ← 1,800 new trades from second drop
├── processed_Polymarket_Copytrade_2026-03-22_091500.csv  ← original, renamed with timestamp
├── processed_Polymarket_Copytrade_2026-03-23_120000.csv  ← all dupes, no ingested_ file created
└── Polymarket_Copytrade.csv                       ← NEW DROP (not yet processed)
```

If the user drops a new `Polymarket_Copytrade.csv` that overlaps with previous ingests, that's fine — dedup on `tx_hash` ensures no double-counting.

### Sharp Sims (`data/sims/`)

The user drops a sharp sim xlsx into this folder.

When `python curator.py ingest-sim` runs (no args):
1. Scans for `.xlsx` files that don't start with `sim_` prefix (i.e., new/unprocessed)
2. Assigns the next sequential sim number from the `sim_registry` table
3. Renames the file to `sim_001_Sharpsim_5b1cbf18.xlsx` (sim number + original name)
4. Ingests the wallet data into SQLite, **forcing `str` dtype on Token ID and Market ID columns in DRL sheets**
5. `sim_date` is set to the current date/time at ingest (use Python `datetime.now()`)

So the folder over time looks like:
```
data/sims/
├── sim_001_Sharpsim_5b1cbf18.xlsx   ← first sim, renamed
├── sim_002_Sharpsim_abc123.xlsx     ← second sim, renamed
├── sim_003_Sharpsim_def456.xlsx     ← third sim, renamed
└── NewSimResults.xlsx               ← NEW DROP (not yet processed)
```

The sim number in the filename matches the sim number in SQLite — the user can always find the original file for any wallet by checking its sim number.

### Active Wallets File

**Location:** `active_wallets.csv` in the project root.

**The user will always provide this file.** It will exist before any commands are run.

**Format:** This is the exact SharpAIO copy-trading CSV format. The user can paste directly from their SharpAIO setup:

```csv
address,enabled,copy_buy,copy_sell,bet_amount,slippage,slippage_mode,buy_slippage_type,market_filter,market_blacklist,market_whitelist,copy_percentage_enabled,copy_percentage,copy_percentage_limit,max_exposure_per_market,buy_protection,min_bet_size,max_bet_size
__global__,true,true,true,200,0.1,absolute,aggressive,,,,true,4,200,400,true,0,
0x82d2e4dbb0a849ff8e2f5380719769145648beea,true,true,true,200,0.1,absolute,aggressive,,,cs2-|csgo|counter-strike,true,4,200,400,true,0,
```

**What the agent reads from this file:**
- `address` column — which wallets are currently active (skip `__global__` row)
- `market_whitelist` column — what game(s) each wallet is filtered to. Parse using the game normalization mapping. Multi-game wallets → `ESPORTS`.

**What the agent IGNORES:**
- All other columns (bet_amount, slippage, copy_percentage, etc.) — these are SharpAIO config parameters, not wallet evaluation data.

The user manually edits this file when they decide to add or remove wallets based on the agent's recommendations.

### Change Ledger

Every time `ingest` runs, the system diffs `active_wallets.csv` against the `last_known_wallets` snapshot table in SQLite and logs any changes. This is snapshot-based, not event-replay — each run overwrites the snapshot with the current CSV state, preventing drift.

**Stored in two places:**

1. **SQLite** `wallet_changes` table — for the agent to query during evaluations
2. **Markdown file** `reports/wallet_changelog.md` — appended for user to read

Changelog format:
```markdown
# Wallet Change Log

## 2026-03-20 14:30:00
- ✅ ADDED 0x82d2e4...beea (CS2)
- ✅ ADDED 0xce0871...510b (LOL)
- ✅ ADDED 0x8d9f81...787a (LOL)
[initial setup — 23 wallets]

## 2026-03-25 09:15:00
- 🟢 ADDED 0xNEWWAL...1234 (DOTA)
- 🔴 REMOVED 0xb624f2...de17 (VALO)
```

This gives a full history of what was in the CSV at any point in time. The agent can query SQLite to see things like "how long was this wallet active before it was removed" and "what wallets have been churned (added then removed)."

---

## Retired Wallet Memory

When a wallet is removed from `active_wallets.csv` (detected by the change ledger), the system automatically creates a **retirement summary** and stores it in Mem0 — **if Mem0 is available**. If Mem0 is not set up yet, the retirement summary is still generated and logged to the change ledger markdown file so the data isn't lost. It will be stored in Mem0 on the next `evaluate` run as a catch-up step.

**On wallet removal, the system:**
1. Queries SQLite for the wallet's full performance data (total P&L, ROI, trade count, game, duration active, sim profile flags)
2. Builds a retirement summary string, e.g.: *"RETIRED 0x82d2e4 (CS2) on 2026-04-15. Was active for 26 days. Performance: +$1,862 realized P&L, 97 unique markets, median entry 48c, grinder profile. Removed reason: unknown (manual). Sharp Sim #1."*
3. Stores in `wallet_changes` table with the full summary text
4. Appends to `reports/wallet_changelog.md`
5. If Mem0 is available: stores in Mem0 with metadata `{"type": "retired_wallet", "wallet": addr}`
6. If Mem0 is NOT available: logs a note that retirement memory is pending

The retirement summary is generated by **Python** (pulling numbers from SQLite), not by the LLM. It's factual data, not a judgment call.

---

## Agent Awareness: "What's New Since Last Evaluation"

The agent needs to know what data it hasn't seen yet. This is handled through timestamps in SQLite:

- `evaluation_log.eval_date` — when the last evaluation ran
- `ingest_registry.ingested_at` — when each Sharp log batch was ingested
- `sim_registry.ingested_at` — when each sharp sim was ingested
- `wallet_changes.change_date` — when wallets were added/removed

When `evaluate` runs, the prompt builder queries what's new:

```sql
-- If there IS a prior evaluation:
SELECT * FROM ingest_registry WHERE ingested_at > (SELECT MAX(eval_date) FROM evaluation_log);
SELECT * FROM sim_registry WHERE ingested_at > (SELECT MAX(eval_date) FROM evaluation_log);
SELECT * FROM wallet_changes WHERE change_date > (SELECT MAX(eval_date) FROM evaluation_log);

-- IMPORTANT: If evaluation_log is EMPTY (first run), MAX() returns NULL
-- and `> NULL` evaluates to FALSE, returning zero rows.
-- On first evaluation, SKIP the WHERE filter entirely — treat ALL data as new.
-- Implementation: check if evaluation_log has any rows first. If not, select all.
```

This gives the agent a "since last time you looked" summary:
- "Since your last evaluation on March 20: 2 new Sharp log batches (3,400 new trades), 1 new sharp sim (#3 with 89 wallets), 1 wallet added (0xNEW), 1 wallet removed (0xOLD)."

On the FIRST evaluation (no prior eval in log), the summary says "This is your first evaluation. Total data: X trades, Y sims, Z active wallets."

---

## Weekly P&L Buckets

For the evaluation prompt, the agent needs to see P&L trajectory over time — not just a single total. Weekly buckets provide this.

**Bucket definition:** Each week is identified by its Monday date. A trade on Wednesday March 19 belongs to the "March 17" bucket (the Monday of that week). Use Python's `date.isocalendar()` or similar to compute the Monday.

**Computation (in `analyzer.py`):**

For each wallet, group all resolved positions + sell trades by the week they occurred:

```
Week of March 3:  +$420 realized (from 12 resolved tokens + 5 sells)
Week of March 10: -$180 realized (from 8 resolved tokens + 3 sells)
Week of March 17: +$95 realized (from 4 resolved tokens + 2 sells)
                  $340 unrealized (6 tokens still open from this week)
```

**Rules:**
- A resolved position's P&L is attributed to the week of the LAST trade in that position (the most recent buy or sell timestamp), not the resolution date. This is because the wallet's decision to enter/exit happened on the trade date.
- Sell P&L is attributed to the week the sell occurred.
- Only compute for the most recent 8 weeks (enough for the agent to see trends without bloating the prompt).
- This is computed on-the-fly by `analyzer.py` from the `trades`, `positions`, and `resolutions` tables — it is NOT stored in a separate table. It's a derived view.

**Format sent to the evaluation prompt:**
```
Wallet 0x82d2e4 (CS2) — Weekly P&L:
  Mar 03: +$420 (12 markets resolved)
  Mar 10: -$180 (8 markets resolved)
  Mar 17: +$95 (4 resolved, 6 still open)
  Trend: recovering after drawdown week
```

The "Trend" line is NOT computed by Python — it's left for the LLM to interpret from the numbers.

---

## Commands

The CLI has five modes:

### `python curator.py ingest`

**No API keys required. No Mem0 required.**

Ingests all unprocessed SharpAIO trade log CSVs from `data/sharp_logs/`.

- Auto-scans for `.csv` files that don't have the `ingested_` or `processed_` prefix (ignores non-csv files like .DS_Store)
- For each unprocessed CSV:
  - Reads the CSV with **`str` dtype forced on Token ID, Tx Hash, Master Wallet columns** (handles malformed rows)
  - Deduplicates against existing trades in SQLite using `tx_hash`
  - If new trades found: saves only the NEW trades to a dated archive file (`ingested_NNN_YYYY-MM-DD.csv`)
  - If NO new trades found: skips archive creation, prints notification
  - Renames the original file with `processed_` prefix + full timestamp (avoids same-day collisions)
  - Inserts new trades into SQLite
- After all files processed, **in this exact order (ordering matters for P&L accuracy):**
  1. **Full rebuild of `positions` table** from all trades (DROP + rebuild from scratch, not incremental). This prevents drift and is fast enough at this data scale.
  2. **Ensure resolution entries exist** for all unique token_ids in `positions` (see Resolution Entry Creation).
  3. **Check all unresolved tokens** (resolved = 0) against the Polymarket Gamma API. Cache resolutions.
  4. **Recompute wallet-level P&L.** This MUST happen AFTER resolution checks — newly resolved tokens need to be counted as realized P&L, not unrealized. If you recompute before resolving, the P&L numbers will be wrong.
- After P&L recompute:
  - Diffs `active_wallets.csv` against last known state and logs any changes
  - If any wallets were REMOVED: generates retirement summaries, stores in Mem0 if available, always logs to changelog
- Prints summary: "Processed X file(s). Ingested Y new trades (Z already existed). W tokens newly resolved."

### `python curator.py ingest-sim`

**No API keys required. No Mem0 required.**

Ingests all unprocessed sharp sim xlsx files from `data/sims/`.

- Auto-scans for `.xlsx` files that don't have the `sim_` prefix (ignores non-xlsx files like .DS_Store)
- For each unprocessed xlsx:
  - Assigns next sequential sim number
  - Renames file to `sim_NNN_originalname.xlsx`
  - Sets `sim_date` to current datetime (`datetime.now()`)
  - Reads wallet data (IGNORING all simulation parameters)
  - **Forces `str` dtype on Token ID, Market ID, Source Trade ID columns in DRL sheets** to prevent precision loss
  - Computes behavioral profiles from DRL drill-downs (with graceful handling of missing/broken sheets)
  - Normalizes token IDs from decimal to hex format
  - Stores in `sim_snapshots` and `sim_profiles` tables
- Prints summary: "Ingested Sharp Sim #X (filename) with Y wallets (Z had complete profiles, W had partial/missing DRL data)"

**Important: The agent ONLY extracts wallet trading data. It IGNORES all simulation parameters: capital, max bet, max bet per market, copy percentage, slippage settings, portfolio sizing.**

**Handling missing/broken DRL sheets:** Not all wallets will have working drill-down sheets. The system must:
- Gracefully skip wallets with missing `_DRL` or `_SUM` sheets
- Store whatever data IS available (e.g., Results sheet data without behavioral profile)
- Set `profile_complete = 0` in `sim_profiles` so the evaluator knows the data is partial
- Log which wallets had incomplete data in the ingest summary
- The agent should note during evaluation when it's working with incomplete profile data

**What to extract from each sharp sim:**

From `📊 Results` sheet (per wallet):
- `Wallet Address`, `Detail` (game — normalize to canonical), `Trades`, `Sim Trades`, `Volume`
- `💰 Sim PnL`, `📈 Sim ROI %`, `📉 Max DD %`
- `✅ Copied`, `⏭️ Skipped`
- `LB All-Time $`, `LB Name`, `Gamma Cash PnL`

From `_DRL` drill-down sheets (per wallet) — compute behavioral flags:
- **Entry price distribution**: median/mean of `Source Price` across all trades
- **P&L concentration**: what % of total P&L comes from the top 1, 2, 3 markets (from `_SUM` sheet)
- **One-hit-wonder score**: ratio of best single market P&L to total P&L
- **Market diversity**: unique markets / total trades ratio
- **Arb detection**: look for buys on both outcome sides of the same market
- **High-price scalping**: % of trades with `Source Price` >= 0.95
- **Hedge frequency**: % of markets where the wallet holds both sides

Use SOURCE columns (`Source Price`, `Source Shares`, `Source Notional $`) — these represent how the wallet actually traded, not the simulated copy.

IGNORE these sheets entirely:
- `ℹ️ Info` — simulation metadata/parameters
- `📦 Portfolio` — portfolio sizing parameters

### `python curator.py evaluate`

**Requires: Anthropic API key + Mem0.**

Runs the full evaluation cycle.

**Scope: The agent only evaluates wallets that are (a) currently in `active_wallets.csv`, or (b) appear in the Sharp logs (meaning they're being copied and have real trade data).** It does NOT evaluate every wallet from every sim.

Steps:
1. Reads `active_wallets.csv` for current wallet list
2. Gets the set of wallets with real trade data from SQLite
3. The evaluation set = union of (active wallets) + (wallets with Sharp log trades)
4. Loads the wallet criteria document (`wallet_criteria.md`)
5. Catches up on any pending retirement summaries that weren't stored in Mem0 during ingest
6. Queries "what's new since last evaluation" from SQLite registries (handles first-run case where evaluation_log is empty)
7. Queries SQLite for each in-scope wallet:
   - Real P&L (from Sharp logs + resolutions)
   - Weekly P&L buckets (last 8 weeks, computed by analyzer.py)
   - Sim profile data if available (behavioral flags, which sim # it came from — show LATEST sim number)
   - Whether the sim profile is complete or partial
   - Change history (when added, has it been churned before)
8. Queries Mem0 for:
   - Wallet-specific memories (including retired wallet profiles for similar wallets)
   - General pattern memories
   - Past decision logs
9. Builds a structured prompt for Claude API containing:
   - The wallet criteria document
   - "What's new since last eval" summary
   - Per-wallet data packets (real P&L, weekly trajectory, sim profile, Mem0 context, latest sim # reference, game, profile completeness flag)
   - The current active wallet list
   - Instructions to output structured JSON recommendations
10. Calls Claude API (claude-sonnet-4-20250514)
11. Parses the response into:
    - **ADD** — wallets not in the CSV that should be added
    - **REMOVE** — wallets in the CSV that should be taken out
    - **KEEP** — wallets in the CSV that are performing well and should stay (active endorsement)
    - **WATCH** — wallets that are borderline
12. Stores the agent's observations and reasoning into Mem0
13. Generates a markdown report in `reports/eval_YYYY-MM-DD_HHMMSS.md`
14. Prints the report to console

### `python curator.py pnl`

**No API keys required. No Mem0 required.**

Displays a running P&L dashboard for all tracked wallets.

Before computing P&L, the `pnl` command runs resolution checks (steps 2-4 from the ingest pipeline): ensure resolution entries exist → check unresolved tokens against Gamma API → recompute P&L. Positions are NOT rebuilt (only rebuilt during `ingest`). This ensures the user always sees up-to-date resolution status.

- Reads `active_wallets.csv` for the "In CSV" column and config game filter
- Queries SQLite for all wallet P&L data
- Shows which sharp sim # each wallet LAST appeared in (latest sim number from `sim_snapshots`)
- **Shows BOTH game sources** so the user can compare:
  - `Filter` column: the game from `active_wallets.csv` market_whitelist (what the wallet is configured to trade)
  - `Actual` column: the most-traded game by trade count from Sharp logs (what the wallet actually trades most)
- Prints to console AND saves to `reports/pnl_YYYY-MM-DD_HHMMSS.md` (timestamped to avoid overwriting on same-day runs)

Output format:
```
Wallet P&L Dashboard — {date}
Data: {X} total trades | {Y} wallets tracked | {Z}/{T} tokens resolved | {U} unresolvable

Wallet               | Filter   | Actual | Sim # | Invested | Realized P&L | Unrealized | Total P&L | Markets | Trades | In CSV
0x82d2e4...beea      | CS2      | CS2    | #1    | $5,240   | +$1,862      | $340 open  | +$1,862   | 97      | 3775   | ✅
0xce0871...510b      | LOL      | LOL    | #1    | $8,100   | +$858        | $120 open  | +$858     | 234     | 5011   | ✅
0x8fe9f7...6dc       | ESPORTS  | CS2    | #1    | $3,400   | +$507        | $50 open   | +$507     | 62      | 948    | ✅
0xb624f2...de17      | VALO     | VALO   | #1    | $2,300   | +$723        | $0         | +$723     | 5       | 125    | ❌
0xNEWWAL...5678      | —        | LOL    | —     | $1,100   | +$200        | $80 open   | +$200     | 15      | 180    | ❌
...

Totals: Invested ${X} | Realized P&L ${Y} | Unrealized ${Z} open
```

Notes:
- `Filter` = `—` if the wallet isn't in `active_wallets.csv` (only appears in Sharp logs)
- `Actual` = most traded game by trade count from Sharp logs. Shows `—` if no Sharp log data
- `Sim #` = `—` if the wallet never appeared in any sim

### `python curator.py status`

**No API keys required. No Mem0 required.**

Quick overview of the system state.

```
Wallet Curator Status
─────────────────────
Database: data/curator.db (exists / not found)
Active wallets: 23 in active_wallets.csv
Sharp logs ingested: 3 files, 8,100 total trades
Sharp sims ingested: 2 files (Sim #1: 154 wallets, Sim #2: 89 wallets)
Tokens tracked: 412 (380 resolved, 32 pending, 5 unresolvable)
Last ingest: 2026-03-22 09:15:00
Last evaluation: 2026-03-22 09:20:00
Since last eval: 1 new ingest (1,200 trades), 0 new sims, 0 wallet changes
Pending in data/sharp_logs/: 1 unprocessed file
Pending in data/sims/: 0 unprocessed files
Malformed rows awaiting repair: 25 (in data/malformed/)
Mem0: configured / not configured
```

### `python curator.py repair`

**Requires: Anthropic API key. No Mem0 required.**

Repairs malformed rows captured during ingest using Claude Sonnet.

- Scans `data/malformed/` for unprocessed `.txt` files
- Each file has the original CSV header as the first line, followed by malformed rows
- Processes malformed rows in batches of 5 (small to prevent one garbled row from confusing the entire repair)
- For each batch: sends the CSV header + 5 example correct rows (from `trades` table) + the malformed rows to Claude Sonnet
- The LLM returns ONLY fixed CSV rows — no explanation
- Each repaired row is validated (correct column count, valid Action, required fields present) before saving
- Valid repaired rows are saved to `data/sharp_logs/repaired_NNN_YYYY-MM-DD.csv` incrementally (partial progress survives interruption)
- After all batches: renames the malformed file to `processed_malformed_...`
- The repaired CSV is picked up by the next `ingest` run through the normal pipeline (dedup handles overlaps)
- Prints: "Repaired X of Y malformed rows. Run `python curator.py ingest` to process them."

### Malformed Row Capture (during `ingest`)

During Sharp log ingest, malformed rows are captured to `data/malformed/` instead of being silently discarded:

- **Structurally malformed** (wrong column count, unclosed quotes): detected by comparing raw line count vs pandas parsed row count. Re-read with Python `csv` module to identify the specific bad lines.
- **Semantically malformed** (parsed OK but bad Action values like tx hashes in the Action column): detected by `Action.isin(['Buy', 'Sell'])` filter. Extracted as CSV lines.
- Both categories saved to `data/malformed/malformed_NNN_YYYY-MM-DD.txt` with the original CSV header as the first line for context.
- Log: "⚠️ X malformed rows saved to data/malformed/ for repair."

---

## P&L Computation

P&L is computed from the `positions` and `resolutions` tables. The formula must handle the partial-sell-then-resolve scenario correctly.

**Positions are fully rebuilt (DROP + recreate) on every ingest** from the `trades` table. This prevents drift from incremental update bugs and is fast enough at this data scale (tens of thousands of trades).

**P&L MUST be recomputed AFTER resolution checks.** The ordering is: rebuild positions → ensure resolution entries → resolve tokens → compute P&L. If P&L is computed before resolutions, newly resolved tokens will be counted as unrealized instead of realized, producing incorrect numbers.

**Per position (one wallet + one token):**

```
Given:
  shares_bought     = total shares purchased across all buys
  total_invested    = total dollars spent on buys
  shares_sold       = total shares sold (before resolution)
  total_received    = total dollars received from sells
  net_shares        = shares_bought - shares_sold (shares held at resolution)

  ** GUARDS (enforced at positions rebuild, not at P&L time) **
  Positions with shares_bought == 0 or net_shares < 0 are NOT stored in the
  positions table at all. They are excluded during the positions rebuild step
  via a HAVING clause. The excluded count per wallet is tracked in
  wallet_pnl.incomplete_positions so the user knows P&L may be understated.
  avg_cost_basis    = total_invested / shares_bought

If token is RESOLVED:
  If token WON (resolved = 1):
    resolution_value = net_shares * 1.00
    cost_basis_of_remaining = avg_cost_basis * net_shares
    resolved_pnl = resolution_value - cost_basis_of_remaining
  If token LOST (resolved = -1):
    resolution_value = 0
    cost_basis_of_remaining = avg_cost_basis * net_shares
    resolved_pnl = 0 - cost_basis_of_remaining

If token is UNRESOLVED (resolved = 0):
  unrealized_invested = avg_cost_basis * net_shares
  (we don't estimate unrealized P&L — just track the dollars at risk)

If token is UNRESOLVABLE (resolved = -2):
  Treat same as UNRESOLVED — money is at risk but status unknown

Sell P&L (from explicit sells, regardless of resolution):
  sell_pnl = total_received - (avg_cost_basis * shares_sold)
```

**Per wallet rollup (`wallet_pnl` table):**

```
total_invested             = SUM of total_invested across all positions
total_received_sells       = SUM of total_received across all positions
total_received_resolutions = SUM of resolution_value for WON tokens
total_lost_resolutions     = SUM of cost_basis_of_remaining for LOST tokens
realized_pnl               = (total_received_sells - SUM(avg_cost_basis * shares_sold))
                           + (total_received_resolutions - SUM(avg_cost_basis * net_shares) for won)
                           + (0 - SUM(avg_cost_basis * net_shares) for lost)
unrealized_shares          = SUM of net_shares where token is unresolved or unresolvable
unrealized_invested        = SUM of (avg_cost_basis * net_shares) where unresolved or unresolvable
```

**Important:** `avg_cost_basis` is used to allocate cost between sold shares and remaining shares. Individual entry prices are preserved in the `trades` table for behavioral analysis, but average cost basis is the correct method for P&L computation on fungible positions.

---

## Resolution API

**Endpoint:** `GET https://gamma-api.polymarket.com/markets?clob_token_ids=TOKEN_ID`

### Resolution Entry Creation

Before checking resolutions with the API, the system must ensure every unique token_id from the `positions` table has a corresponding row in the `resolutions` table.

**Flow:**
1. After rebuilding `positions`, query: `SELECT DISTINCT token_id, market, outcome FROM positions WHERE token_id NOT IN (SELECT token_id FROM resolutions)`
2. For each new token_id, INSERT into `resolutions` with `resolved = 0`, populating `market` and `outcome` from the position data
3. Now the resolver can simply query `SELECT * FROM resolutions WHERE resolved = 0` to get its work list

**Why this matters:** The `resolutions` table already knows which `outcome` each token represents (from the trade data). When the Gamma API returns which outcome WON the market, the resolver just compares: does this token's stored `outcome` match the winning outcome? If yes → resolved = 1. If no → resolved = -1. This avoids the resolver needing extra logic to figure out which side of the market a token is on.

### How Resolution Works (multi-step)

1. Get the list of unresolved tokens from `resolutions` table (resolved = 0, checked_at not within last hour)
2. For each token, query the Gamma API with the token ID
3. The API returns market data including whether the market is resolved and which outcome won
4. Compare the winning outcome against the token's `outcome` field (already stored in `resolutions` from step above)
5. If match → resolved = 1 (won, worth $1)
6. If no match → resolved = -1 (lost, worth $0)
7. If market not yet resolved → leave as resolved = 0, update `checked_at`
8. If API returns no data → try CLOB API fallback. If both fail → resolved = -2 (unresolvable)

**Fallback:** CLOB API at `https://clob.polymarket.com`

**Batching / Rate Limiting:**
Batch lookup is supported via repeated `clob_token_ids` query params (NOT comma-separated). Example: `?clob_token_ids=DECIMAL1&clob_token_ids=DECIMAL2&...`. Tested with batch size 50 — returns results in ~0.23s. Use batch size 50 tokens per request with 50ms courtesy delay between batches. No rate limiting was observed in testing.

On first ingest there may be hundreds of unresolved tokens. Build in progress logging so the user knows it's working ("Checking resolutions... 50/412 done").

**Token ID format:** Always query using DECIMAL format. The Gamma API rejects hex-formatted token IDs ("invalid clob token ids" error). Convert with `token_id_to_decimal()` before querying. Hex is only used as the canonical internal storage format.

**Caching:**
- Only query tokens with resolved = 0 in `resolutions` table
- Once resolved (1 or -1), cache permanently (never re-fetch)
- For unresolved tokens (0), skip if `checked_at` is within last hour
- For unresolvable tokens (-2), skip entirely (don't keep hammering a broken lookup)

---

## SQLite Schema

Database file: `data/curator.db`

```sql
-- Raw trade log entries from SharpAIO
-- Every individual trade with exact execution price is preserved
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash TEXT UNIQUE NOT NULL,
    timestamp TEXT NOT NULL,
    master_wallet TEXT NOT NULL,       -- ALWAYS lowercase
    own_wallet TEXT NOT NULL,
    action TEXT NOT NULL,              -- 'Buy' or 'Sell'
    market TEXT NOT NULL,
    outcome TEXT NOT NULL,
    token_id TEXT NOT NULL,            -- ALWAYS hex format, lowercase, 0x-prefixed
    price REAL NOT NULL,               -- exact execution price for THIS trade
    shares REAL NOT NULL,
    invested REAL NOT NULL,
    received REAL NOT NULL,
    pnl_pct REAL,
    pct_sold REAL,
    reason TEXT,
    game TEXT,                         -- canonical: CS2, LOL, DOTA, VALO, UNKNOWN
    ingest_batch INTEGER,              -- which ingested_NNN file this came from
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_trades_wallet ON trades(master_wallet);
CREATE INDEX idx_trades_token ON trades(token_id);
CREATE INDEX idx_trades_timestamp ON trades(timestamp);
CREATE INDEX idx_trades_game ON trades(game);

-- Computed positions (FULL REBUILD from trades on every ingest — DROP + recreate)
-- Only positions where total_shares_bought > 0 AND net_shares >= 0 are stored.
-- Positions with zero buys or negative net shares (sells without matching buys)
-- are excluded entirely — no meaningful P&L can be computed for them.
-- The excluded count per wallet is tracked in wallet_pnl.incomplete_positions.
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_wallet TEXT NOT NULL,       -- ALWAYS lowercase
    token_id TEXT NOT NULL,            -- ALWAYS hex format
    market TEXT NOT NULL,
    outcome TEXT NOT NULL,
    game TEXT,                         -- canonical game name
    total_shares_bought REAL NOT NULL DEFAULT 0,
    total_invested REAL NOT NULL DEFAULT 0,
    total_shares_sold REAL NOT NULL DEFAULT 0,
    total_received REAL NOT NULL DEFAULT 0,
    net_shares REAL NOT NULL DEFAULT 0,
    avg_cost_basis REAL,              -- total_invested / shares_bought (NULL if shares_bought = 0)
    last_updated TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(master_wallet, token_id)
);

CREATE INDEX idx_positions_wallet ON positions(master_wallet);

-- Token resolution cache (permanent once resolved)
-- Rows are CREATED during ingest when new token_ids are found in positions.
-- market and outcome are populated from trade data at creation time.
-- The resolver then only needs to check if the market resolved and compare outcomes.
CREATE TABLE resolutions (
    token_id TEXT PRIMARY KEY,         -- ALWAYS hex format
    market TEXT,                       -- populated from trade data when row is created
    outcome TEXT,                      -- which outcome this token represents (from trade data)
    resolved INTEGER NOT NULL DEFAULT 0,  -- 0=unresolved, 1=won($1), -1=lost($0), -2=unresolvable
    resolution_price REAL,                -- 1.0 or 0.0 or NULL
    resolved_at TEXT,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Wallet P&L summary (recomputed after each ingest, AFTER resolution checks)
CREATE TABLE wallet_pnl (
    master_wallet TEXT PRIMARY KEY,     -- ALWAYS lowercase
    game TEXT,                         -- most-traded canonical game name by trade count
    total_invested REAL NOT NULL DEFAULT 0,
    total_received_sells REAL NOT NULL DEFAULT 0,
    total_received_resolutions REAL NOT NULL DEFAULT 0,
    total_lost_resolutions REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_shares REAL NOT NULL DEFAULT 0,
    unrealized_invested REAL NOT NULL DEFAULT 0,
    unique_markets INTEGER NOT NULL DEFAULT 0,
    unique_tokens INTEGER NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    incomplete_positions INTEGER NOT NULL DEFAULT 0,  -- count of positions with net_shares < 0
    first_trade TEXT,
    last_trade TEXT,
    last_computed TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sharp sim registry (maps sequential numbers to filenames)
CREATE TABLE sim_registry (
    sim_number INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    renamed_filename TEXT NOT NULL,
    sim_date TEXT NOT NULL,            -- datetime.now() at time of ingest
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    wallet_count INTEGER
);

-- Sharp sim snapshot data (from Results sheet)
-- ONLY wallet trading data — NO simulation parameters
CREATE TABLE sim_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_number INTEGER NOT NULL,
    wallet_address TEXT NOT NULL,       -- ALWAYS lowercase
    category TEXT,
    subcategory TEXT,
    detail TEXT,                        -- canonical game name
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

CREATE INDEX idx_sim_wallet ON sim_snapshots(wallet_address);
CREATE INDEX idx_sim_number ON sim_snapshots(sim_number);

-- Computed behavioral profiles from DRL drill-downs
CREATE TABLE sim_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_number INTEGER NOT NULL,
    wallet_address TEXT NOT NULL,       -- ALWAYS lowercase
    detail TEXT,                        -- canonical game name
    profile_complete INTEGER DEFAULT 1, -- 0 if DRL/SUM sheets were missing or broken
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

CREATE INDEX idx_profile_wallet ON sim_profiles(wallet_address);

-- Sharp log ingest registry
CREATE TABLE ingest_registry (
    batch_number INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    archive_filename TEXT NOT NULL,     -- 'SKIPPED' if all dupes
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    new_trades INTEGER NOT NULL DEFAULT 0,
    duplicate_trades INTEGER NOT NULL DEFAULT 0
);

-- Active wallet change ledger
CREATE TABLE wallet_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_date TEXT NOT NULL DEFAULT (datetime('now')),
    wallet_address TEXT NOT NULL,       -- ALWAYS lowercase
    action TEXT NOT NULL,               -- 'ADDED' or 'REMOVED'
    game_filter TEXT,                   -- canonical game name at time of change
    trigger TEXT DEFAULT 'manual',
    retirement_summary TEXT             -- populated on REMOVED, factual performance summary
);

CREATE INDEX idx_changes_wallet ON wallet_changes(wallet_address);
CREATE INDEX idx_changes_date ON wallet_changes(change_date);

-- Snapshot of active_wallets.csv for change detection (overwritten each run)
CREATE TABLE last_known_wallets (
    wallet_address TEXT PRIMARY KEY,    -- ALWAYS lowercase
    game_filter TEXT,                   -- canonical game name
    snapshot_date TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Agent evaluation history
CREATE TABLE evaluation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_date TEXT NOT NULL DEFAULT (datetime('now')),
    wallets_evaluated INTEGER,
    adds_recommended INTEGER,
    removes_recommended INTEGER,
    keeps_recommended INTEGER,
    watches_recommended INTEGER,
    report_path TEXT,
    raw_response TEXT
);
```

---

## Sharp Log CSV Format

File: `Polymarket_Copytrade.csv` (always this name from the VPS)

Columns:
- `Date` — ISO timestamp
- `Master Wallet` — the wallet being copied (normalize to lowercase)
- `Own Wallet` — the user's wallet
- `Action` — `Buy` or `Sell` (skip malformed rows)
- `Market` — human-readable name (extract canonical game from prefix)
- `Outcome` — the outcome being bought/sold
- `Token ID` — Polymarket CLOB token ID (normalize to hex, lowercase)
- `Price` — execution price (0.00 to 1.00)
- `Shares` — number of shares
- `Invested` — dollars invested (buys)
- `Received` — dollars received (sells)
- `PNL %` — P&L percentage (sells only)
- `% Sold` — percentage of position sold
- `Reason` — trade reason
- `Tx Hash` — transaction hash (dedup key)

Parsing notes:
- Use `on_bad_lines='skip'` with pandas
- **Force `str` dtype on `Token ID`, `Tx Hash`, and `Master Wallet` columns** to prevent numeric coercion of large hex strings
- Filter to only `Action == 'Buy'` or `Action == 'Sell'`
- Normalize wallet addresses to lowercase
- Normalize token IDs to hex format
- Extract canonical game name from `Market` field prefix (fallback to `UNKNOWN` if no match)

---

## Sharp Sim XLSX Format

Sheet types:
- `ℹ️ Info` — **IGNORE entirely**
- `📊 Results` — one row per wallet, extract trading data only, **IGNORE parameter columns**
- `📦 Portfolio` — **IGNORE entirely**
- `XX_0xABCDEF_GAME_SUM` — per-wallet market-level P&L breakdown
- `XX_0xABCDEF_GAME_DRL` — per-wallet trade-level drill-down

**Critical: The sharp sim is a BACKTEST. The agent IGNORES all simulation parameters. It only evaluates the WALLET'S trading behavior.**

**Token IDs in DRL sheets are in DECIMAL format** — must be converted to hex (0x-prefixed, lowercase) using `normalize_token_id()` before storing. **Force `str` dtype on Token ID, Market ID, and Source Trade ID columns** when reading DRL sheets with pandas to prevent precision loss on large numbers.

DRL header row is at a variable position. Find it dynamically by searching for the row containing `Timestamp (UTC)` in the first column. Do not hardcode the row index. Columns: `Timestamp (UTC)`, `Status`, `Side`, `Market ID`, `Category`, `Subcategory`, `Detail`, `Event`, `Question`, `Source Trade ID`, `Token ID`, `Source Price`, `Source Shares`, `Source Notional $`, `Source Fee $`, `Copied Price`, `Copied Shares`, `Copied Notional $`, `Copied Fee $`, `Skip Reason`.

Use SOURCE columns for behavioral analysis.

**Missing/broken DRL sheets:** Set `profile_complete = 0` and continue.

---

## Mem0 Integration

Use `mem0ai` Python SDK. Local mode (no API key needed) or cloud mode.

**Only required for `evaluate` command.** All other commands work without Mem0.

**Four memory types (all written automatically, not by the user):**

1. **Wallet observations** — tagged to specific addresses. Written by LLM during evaluation.
2. **Pattern learnings** — generalizable insights. Written by LLM during evaluation.
3. **Decision logs** — recommendations and reasoning. Written by LLM during evaluation.
4. **Retired wallet profiles** — factual performance summaries. Written by Python when change ledger detects removal. Catch-up stored during next `evaluate` if Mem0 wasn't available at removal time.

---

## Evaluation Prompt Structure

```
System: You are a Polymarket esports copy-trading wallet curator agent.
Your job is to recommend which wallets to add to, remove from, or keep
in the copy-trading CSV based on data analysis and your accumulated knowledge.

[Contents of wallet_criteria.md]

You will receive:
1. A "what's new" summary showing data changes since your last evaluation
2. Per-wallet data packets with real P&L, weekly trajectory, sim profiles, and behavioral flags
3. Each wallet's latest sharp sim reference number
4. A flag indicating if the sim profile is complete or partial
5. Relevant memories from your past evaluations (including retired wallet profiles)
6. The current active wallet list with their game filters

Output JSON:
{
  "adds": [{"wallet": "0x...", "game": "CS2", "sim_number": 1, "reasoning": "..."}],
  "removes": [{"wallet": "0x...", "game": "LOL", "sim_number": 1, "reasoning": "..."}],
  "keeps": [{"wallet": "0x...", "game": "CS2", "sim_number": 1, "reasoning": "..."}],
  "watch": [{"wallet": "0x...", "game": "VALO", "sim_number": 1, "concerns": "..."}],
  "pattern_observations": ["new general patterns noticed"],
  "wallet_observations": [{"wallet": "0x...", "observation": "..."}]
}

KEEP = wallet is in CSV and performing well, actively endorsed to stay.
pattern_observations and wallet_observations get stored in long-term memory.
Write them as notes to your future self — be specific, include data points.
If a wallet has an incomplete sim profile, note that in your reasoning.
If a wallet has incomplete position data (sells without matching buys), note that P&L may be understated.

User: [what's new summary + structured wallet data with weekly buckets + memories + current active wallets]
```

---

## Report Formats

### Evaluation Report (`reports/eval_YYYY-MM-DD_HHMMSS.md`)

```markdown
# Wallet Curator Report — {date}

**Data basis:**
- Sharp logs: {X} total trades across {Y} wallets
- Latest sharp sim: #{sim_number} ({Z} wallets)
- Resolved tokens: {R} of {T} total ({U} unresolvable)
- Mem0 memories referenced: {M}
- New since last eval: {A} ingests ({B} trades), {C} sims, {D} wallet changes

## KEEP ({count})
✅ **0xABCD...** (CS2) — Sharp Sim #{N}
> {reasoning}
> Real P&L: ${X} over {Y} days | Sim ROI: {Z}% | Markets: {N}
> Weekly: +$420, -$180, +$95 (last 3 weeks)

## ADD ({count})
🟢 **0xEFGH...** (CS2) — Sharp Sim #{N}
> {reasoning}

## REMOVE ({count})
🔴 **0xIJKL...** (VALO) — Sharp Sim #{N}
> {reasoning}

## WATCH ({count})
🟡 **0xMNOP...** (LOL) — Sharp Sim #{N} [⚠️ incomplete profile]
> {concerns}

## Pattern Observations
- {pattern 1}

## Memory Updates
- {X} wallet observations stored
- {Y} pattern learnings stored
- Decision log entry #{Z}
```

### P&L Dashboard (`reports/pnl_YYYY-MM-DD_HHMMSS.md`)

Timestamped to avoid overwriting on same-day runs.

```markdown
# Wallet P&L Dashboard — {date}
Data: {X} trades | {Y} wallets | {Z}/{T} tokens resolved | {U} unresolvable

| Wallet | Filter | Actual | Sim # | Invested | Realized | Unrealized | Total P&L | Markets | Trades | In CSV |
|--------|--------|--------|-------|----------|----------|------------|-----------|---------|--------|--------|
| 0x82d2... | CS2 | CS2 | #1 | $5,240 | +$1,862 | $340 open | +$1,862 | 97 | 3775 | ✅ |
| 0x8fe9... | ESPORTS | CS2 | #1 | $3,400 | +$507 | $50 open | +$507 | 62 | 948 | ✅ |
| 0xNEW... | — | LOL | — | $1,100 | +$200 | $80 open | +$200 | 15 | 180 | ❌ |

Totals: Invested ${X} | Realized ${Y} | Unrealized ${Z}
```

- `Filter` = game from active_wallets.csv market_whitelist (what it's configured to trade). `—` if not in CSV.
- `Actual` = most traded game by trade count from Sharp logs. `—` if no Sharp log data.
- `Sim #` = latest sim the wallet appeared in. `—` if never in a sim.

---

## Error Handling

**General principle: never crash and lose data, never silently skip errors that affect P&L accuracy.**

- **Gamma API unreachable during ingest:** Log a warning, skip resolution checks, continue with ingest. Print "⚠️ Resolution API unreachable — X tokens remain unresolved. Run ingest again later to retry."
- **Gamma API returns unexpected data:** Log the token ID and response, mark as unresolvable (-2), continue.
- **Claude API fails during evaluate:** Print the error, save whatever partial data was prepared to a debug file, do NOT store anything in Mem0 (incomplete evaluation shouldn't pollute memory).
- **Mem0 unavailable during evaluate:** Print error and abort evaluation. The agent should not evaluate without memory — it would lose continuity.
- **Mem0 unavailable during ingest:** Continue normally. Retirement summaries are stored in SQLite regardless. Print "⚠️ Mem0 not available — retirement memories will be stored on next evaluate run."
- **Malformed CSV rows:** Skip with `on_bad_lines='skip'`, log count of skipped rows.
- **Malformed xlsx sheets:** Log which sheets failed, continue with available data, set `profile_complete = 0`.
- **Division by zero in P&L:** Skip the position, log a warning with the wallet + token details.
- **Negative net_shares:** Flag position as `incomplete_data = 1`, exclude from P&L, log warning. Track count in `wallet_pnl.incomplete_positions`.
- **Unknown game name:** Normalize to `UNKNOWN`, log warning so mapping can be extended.
- **Scientific notation token IDs:** If `normalize_token_id()` hits the float fallback path, log a prominent warning: "⚠️ Token ID was in scientific notation — precision loss likely. Check that dtype=str is being enforced on ID columns." The resulting token ID will likely be wrong. This is a signal that the pandas dtype enforcement isn't working.
- **File permission errors:** Print clear error message, don't partially rename files.
- **Non-target files in scan folders:** Ignore files that don't match expected extensions (`.csv` for sharp_logs, `.xlsx` for sims). Do not process or rename them.
- **SQLite write failures:** These are critical — print error and stop. Don't continue with a potentially corrupt database.
- **Empty ingest (all duplicates):** Skip archive file creation, still register in `ingest_registry` with `archive_filename = 'SKIPPED'`, print info message.
- **Pandas numeric coercion:** Force `str` dtype on all ID columns (Token ID, Tx Hash, Market ID, Source Trade ID) to prevent silent precision loss on large numbers.

---

## Dependencies

```
anthropic
mem0ai
pandas
openpyxl
requests
```

---

## File Structure

```
wallet-curator/
├── curator.py                  # Main CLI entry point
├── wallet_criteria.md          # Wallet evaluation criteria doc
├── active_wallets.csv          # Current SharpAIO wallet CSV (user maintains)
├── data/
│   ├── curator.db              # SQLite database (auto-created)
│   ├── sharp_logs/             # Drop .csv files here (only .csv processed)
│   │   ├── ingested_001_...csv # Archived new-trades-only files
│   │   └── processed_...csv   # Renamed originals (with timestamp)
│   └── sims/                   # Drop .xlsx files here (only .xlsx processed)
│       └── sim_001_...xlsx     # Renamed with sim number
├── reports/
│   ├── eval_...md              # Evaluation reports (timestamped)
│   ├── pnl_...md               # P&L dashboards (timestamped)
│   └── wallet_changelog.md     # Running change log (appended)
├── lib/
│   ├── __init__.py
│   ├── db.py                   # SQLite schema, connection, queries
│   ├── normalizers.py          # normalize_game(), normalize_token_id(), normalize_wallet()
│   ├── ingest_sharp.py         # Sharp log CSV parsing and ingestion
│   ├── ingest_sim.py           # Sharp sim xlsx parsing and ingestion
│   ├── resolver.py             # Polymarket API resolution checker + entry creation
│   ├── analyzer.py             # Wallet profile computation + weekly P&L buckets
│   ├── evaluator.py            # Claude API + prompt building
│   ├── memory.py               # Mem0 wrapper (graceful when unavailable)
│   ├── pnl.py                  # P&L computation + dashboard generation
│   ├── changelog.py            # Active wallet change detection, logging, retirement summaries
│   └── file_manager.py         # File scanning (extension-filtered), renaming, archiving, collision handling
├── requirements.txt
└── README.md
```

---

## Key Design Decisions

1. **Python does math, LLM does judgment.** Never send raw data to Claude. Pre-compute everything.
2. **SQLite is the single source of truth.** Everything queryable, everything persistent.
3. **Dedup on tx_hash.** Overlapping Sharp logs are safe — no double-counting.
4. **Resolution caching is permanent.** Once resolved, never re-fetch. Unresolvable tokens flagged and skipped.
5. **Resolution entries created from trade data.** The `resolutions` table knows each token's outcome from the trade, so the resolver just compares against the market's winning outcome.
6. **All wallet addresses are lowercase.** Normalized on ingest everywhere.
7. **All game names use canonical form.** CS2, LOL, DOTA, VALO, ESPORTS, UNKNOWN — everywhere, always.
8. **All token IDs use hex format.** Decimal token IDs from sims are converted to hex on ingest. Scientific notation fallback exists but logs warnings — it means dtype enforcement failed.
9. **All ID columns read as strings.** Force `str` dtype in pandas to prevent silent precision loss on large numbers. The scientific notation fallback in `normalize_token_id()` is a safety net, not a reliable path.
10. **Mem0 is only required for evaluate.** All other commands work without it.
11. **Reports are the user-facing output.** User reads reports, decides, edits CSV manually.
12. **No live automation.** Local tool, run manually, no cron.
13. **Sharp sim parameters are invisible.** Capital, max bet, copy % — agent never sees these.
14. **Sharp sim numbering for human reference.** User can always find the original xlsx. Dashboard shows LATEST sim number.
15. **Individual entry prices preserved.** Every trade at its exact price in `trades` table.
16. **P&L tracking is independent of evaluation.** `ingest`, `pnl`, `status` work without any API keys.
17. **Change ledger tracks everything.** Removals generate retirement summaries stored in SQLite always, Mem0 when available.
18. **Drop-and-ingest pattern.** Extension-filtered scanning. Timestamps in processed filenames prevent collisions. Empty ingests skip archive creation.
19. **Evaluation scope is bounded.** Only active wallets + wallets with Sharp log data.
20. **Agent knows what's new.** Timestamp-based queries tell the agent exactly what changed since last eval. First-run case handled explicitly.
21. **Graceful degradation.** Missing DRL sheets, unresolvable tokens, partial profiles, API failures, negative net_shares, unknown games, scientific notation — the system continues and flags what's incomplete.
22. **Never crash and lose data.** Error handling is explicit. SQLite failures are the only hard stop.
23. **Full positions rebuild.** Positions table is DROP + recreated from trades on every ingest. Prevents drift.
24. **P&L recompute ordering is explicit.** Positions → Resolution entries → Resolve tokens → P&L. Never recompute P&L before resolutions.
25. **Weekly P&L buckets.** Computed on-the-fly for last 8 weeks, attributed to Monday of each week. Gives the agent trajectory context.
26. **Dual game display.** P&L dashboard shows both the configured filter game AND the actual most-traded game so the user can compare.
27. **All report files are timestamped.** Both eval and pnl reports include HHMMSS to prevent same-day overwrites.