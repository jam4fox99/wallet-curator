# Wallet Curator

A local Python CLI tool for curating a Polymarket esports copy-trading wallet list. Ingests SharpAIO trade logs, tracks wallet P&L via SQLite, resolves market outcomes via the Polymarket API, and uses Claude + Mem0 to make add/remove/keep recommendations.

## Setup

```bash
cd wallet-curator
pip install -r requirements.txt
```

### API Keys (optional)

Most commands need **no API keys**. Only `evaluate` and `repair` require them:

```bash
# Claude API — needed for evaluate and repair
export ANTHROPIC_API_KEY=sk-ant-...

# Mem0 — only needed for evaluate (local mode needs no key)
# If using Mem0 cloud: export MEM0_API_KEY=...
```

## Quick Start

```bash
# 1. Drop your Sharp log CSV into data/sharp_logs/
#    (file: Polymarket_Copytrade.csv from VPS)

# 2. Ingest trades + resolve markets + compute P&L
python curator.py ingest

# 3. View P&L dashboard
python curator.py pnl

# 4. Check system status
python curator.py status

# 5. Drop a Sharp sim xlsx into data/sims/
python curator.py ingest-sim

# 6. Run evaluation (needs API keys + Mem0)
python curator.py evaluate
```

## Commands

### `python curator.py ingest`

**No API keys needed.**

Ingests all unprocessed `.csv` files from `data/sharp_logs/`.

- Auto-scans for new CSVs (ignores already-processed files)
- Deduplicates against existing trades using transaction hash
- Normalizes wallet addresses (lowercase), token IDs (hex), game names (CS2/LOL/DOTA/VALO)
- Rebuilds positions, resolves tokens via Polymarket API, computes P&L
- Captures malformed rows to `data/malformed/` for later repair
- Detects changes in `active_wallets.csv` and logs to changelog
- Archives processed files in place (never deletes anything)

**Workflow:** Drop `Polymarket_Copytrade.csv` into `data/sharp_logs/` → run `ingest` → done. Overlapping CSVs are safe — dedup prevents double-counting.

### `python curator.py ingest-sim`

**No API keys needed.**

Ingests all unprocessed `.xlsx` sim files from `data/sims/`.

- Reads wallet data from the Results sheet
- Computes behavioral profiles from SUM/DRL drill-down sheets (entry prices, P&L concentration, arb/scalp detection)
- Renames files with sequential sim numbers (`sim_001_...`)
- Ignores all simulation parameters (capital, slippage, etc.)

### `python curator.py pnl`

**No API keys needed.**

Displays a P&L dashboard for all tracked wallets.

- Updates token resolutions before displaying (always shows fresh data)
- Shows both **Filter** game (from CSV config) and **Actual** game (most traded)
- Shows which Sharp Sim # each wallet appeared in
- Flags wallets with excluded positions (missing buy data)
- Saves report to `reports/pnl_YYYY-MM-DD_HHMMSS.md`

### `python curator.py status`

**No API keys needed.**

Quick overview: trade counts, resolution stats, pending files, last ingest/eval timestamps.

### `python curator.py evaluate`

**Requires: `ANTHROPIC_API_KEY` + Mem0.**

Runs the full evaluation cycle using Claude to recommend wallet adds/removes/keeps.

- Builds per-wallet profiles with weekly P&L, sim data, and behavioral flags
- Queries Mem0 for historical context and pattern memories
- Outputs structured recommendations with reasoning
- Stores observations in Mem0 for continuity across runs
- Saves report to `reports/eval_YYYY-MM-DD_HHMMSS.md`

### `python curator.py repair`

**Requires: `ANTHROPIC_API_KEY`.**

Repairs malformed CSV rows captured during ingest using Claude Sonnet.

- Processes rows in small batches (5 per LLM call) to limit error propagation
- Validates repaired rows before saving (column count, valid Action, required fields)
- Saves output to `data/sharp_logs/repaired_NNN_YYYY-MM-DD.csv`
- Run `ingest` after to process the repaired trades

## File Layout

```
wallet-curator/
├── curator.py                  # CLI entry point
├── active_wallets.csv          # Your SharpAIO wallet CSV (you maintain this)
├── wallet_criteria.md          # Evaluation criteria for the LLM
├── spec.md                     # Full project specification
├── requirements.txt
├── data/
│   ├── curator.db              # SQLite database (auto-created)
│   ├── sharp_logs/             # Drop .csv trade logs here
│   │   ├── ingested_001_...csv # Archived new-trades-only
│   │   └── processed_...csv   # Renamed originals
│   ├── sims/                   # Drop .xlsx sim files here
│   │   └── sim_001_...xlsx     # Renamed with sim number
│   └── malformed/              # Captured malformed rows
├── reports/
│   ├── pnl_...md               # P&L dashboards
│   ├── eval_...md              # Evaluation reports
│   └── wallet_changelog.md     # Running change log
└── lib/                        # Source modules
```

## Key Concepts

**active_wallets.csv** — This is your live SharpAIO config file. The agent reads the `address` and `market_whitelist` columns, ignores everything else. You edit this file manually based on the agent's recommendations.

**Drop-and-ingest** — Drop files into the right folder, run the command. Files are renamed after processing (never deleted). Same file can be dropped multiple times safely.

**P&L accuracy** — Positions with sells but no matching buys (from before logging started) are excluded from P&L and flagged. The dashboard shows footnotes when this happens.

**Resolution** — Market outcomes are checked against the Polymarket Gamma API. Resolved tokens are cached permanently. Unresolved tokens are rechecked each time you run `pnl` or `ingest`.

**Game normalization** — All games map to: `CS2`, `LOL`, `DOTA`, `VALO`, `ESPORTS` (multi-game), or `UNKNOWN`.
