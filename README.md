# Wallet Curator

Wallet Curator is now a cloud-ready Polymarket P&L system:

- Railway-hosted Dash dashboard
- Postgres-backed hourly pipeline
- Standalone VPS sync script for SharpAIO trade ingestion
- CLI fallback that still works against SQLite when `DATABASE_URL` is unset

## Install

```bash
pip install -r requirements.txt
```

## Environment

Copy `.env.example` and fill in the values you need.

- Railway app: `DATABASE_URL`, `DASH_USERNAME`, `DASH_PASSWORD`
- VPS sync: `DATABASE_URL`, `SHARP_*`, sync intervals

The VPS sync script only makes outbound connections to Railway Postgres. No inbound ports are required.

## Run The Dashboard

```bash
python app.py
```

The app binds to `PORT` when Railway provides it, otherwise it defaults to `8050`.

## VPS Sync Script

```bash
pip install psycopg2-binary
python sync_script.py
```

Before the first VPS run, verify the exact `active_wallets.csv` location and set `SHARP_WALLETS_SUBPATH` accordingly.

## CLI Commands

```bash
python curator.py ingest
python curator.py pnl
python curator.py status
python curator.py run
```

- `ingest`: ingests local Sharp CSV files from `data/sharp_logs/`, then runs the shared pipeline
- `pnl`: runs the shared pipeline and prints a CLI P&L summary
- `status`: shows backend, trade, sync, and pipeline status
- `run`: convenience wrapper that ingests local files if present, then refreshes the pipeline

When `DATABASE_URL` is set, the CLI uses Postgres. Otherwise it falls back to `data/curator.db`.

## Deployment

`railway.toml` is included for Railway deployment:

- Build: `pip install -r requirements.txt`
- Start: `python app.py`

## Deprecated Modules

Deprecated evaluator, sim-ingest, repair, and memory-era modules live in `lib/_archived/`.
