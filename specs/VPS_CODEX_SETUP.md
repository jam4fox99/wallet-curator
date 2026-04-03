# VPS Codex Setup Handoff

Paste the prompt below into a Codex session running directly on the Windows VPS.

This handoff assumes:

- Codex has full filesystem access on the VPS
- Codex is already authenticated with GitHub on the VPS
- Codex is allowed to clone the repo and make the local setup changes needed to run the sync worker

Do not run the worker from `Downloads`. Sharp files stay in `Downloads`; the repo and script should live in a separate working folder.

## Paste This Into The VPS Codex Session

```md
You are running directly on a Windows VPS that hosts SharpAIO.

Your job is to fully set up and start the Wallet Curator VPS sync worker from the `cloud-dashboard` branch of this repo:

https://github.com/jam4fox99/wallet-curator

## End goal

Get `sync_script.py` running on this VPS so it:

- reads Sharp trade CSV data every 5 minutes
- syncs new trades into Railway Postgres
- syncs active wallets hourly
- updates `sync_status`
- runs without needing any inbound ports

The Railway dashboard app is already deployed. This task is only for the VPS sync worker.

## Critical rules

1. Verify the exact `active_wallets.csv` path on disk before finalizing.
   - Likely path:
     - `{version_folder}\config\polymarket_csv\active_wallets.csv`
   - Likely trade CSV path:
     - `{version_folder}\config\trade_result_csv\polymarket_copytrade.csv`
   - Do not assume the wallet CSV path. Verify it on disk.

2. Do not add extra Python deps beyond `psycopg2-binary`.
   - `sync_script.py` already supports manual `.env` loading.
   - Do not add `python-dotenv`.

3. Dedup behavior is required.
   - `last_sync.txt` is only an optimization.
   - The actual duplicate safety must come from Postgres with `INSERT ... ON CONFLICT (tx_hash) DO NOTHING`.

4. This worker uses outbound connections only.
   - Do not open firewall ports.
   - Do not configure inbound networking for this script.

5. Use a clean working folder outside `Downloads`.
   - Do not run this from `C:\Users\Administrator\Downloads`
   - `Downloads` is for Sharp files only
   - Use:
     - `C:\Users\Administrator\wallet-curator`

6. Use the real repo and real script.
   - Do not fabricate or rewrite `sync_script.py` unless something is actually broken.
   - Clone the repo and use the checked-in script from the `cloud-dashboard` branch.

## Required secret

You need the Railway Postgres connection string:

```text
DATABASE_URL=postgresql://...
```

If it is not already available on the VPS, ask the user for it before finishing `.env`.

## Working folder

Use this exact folder for the repo checkout:

```text
C:\Users\Administrator\wallet-curator
```

That means the expected working files are:

```text
C:\Users\Administrator\wallet-curator\sync_script.py
C:\Users\Administrator\wallet-curator\.env
```

Do not move the script into `Downloads`.

## First step: get the repo and switch to the correct branch

If the repo does not already exist, run:

```powershell
cd C:\Users\Administrator
git clone https://github.com/jam4fox99/wallet-curator.git
cd C:\Users\Administrator\wallet-curator
git checkout cloud-dashboard
```

If the repo already exists, update it instead:

```powershell
cd C:\Users\Administrator\wallet-curator
git fetch origin
git checkout cloud-dashboard
git pull
```

Then verify all of the following:

1. current working directory is:

```text
C:\Users\Administrator\wallet-curator
```

2. `sync_script.py` exists in that repo
3. current branch is `cloud-dashboard`

If clone or auth fails, stop and report the exact Git error.

## Python requirement

Check Python first:

```powershell
python --version
```

Require Python 3.10 or newer.

If Python is missing or too old, stop and report that clearly.

## Install dependency

Install exactly this dependency:

```powershell
pip install psycopg2-binary
```

Do not add `python-dotenv`.

## Inspect Sharp directories

You must verify the actual file locations on disk.

Use commands like these:

```powershell
cd C:\Users\Administrator\wallet-curator
git branch --show-current
dir C:\Users\Administrator\wallet-curator
dir C:\Users\Administrator\Downloads
Get-ChildItem -Path C:\Users\Administrator\Downloads -Directory
Get-ChildItem -Path C:\Users\Administrator\Downloads -Recurse -Filter polymarket_copytrade.csv
Get-ChildItem -Path C:\Users\Administrator\Downloads -Recurse -Filter active_wallets.csv
```

If there are versioned Sharp folders, identify the highest one and inspect likely paths directly too:

```powershell
dir C:\Users\Administrator\Downloads\<VERSION>\config\trade_result_csv
dir C:\Users\Administrator\Downloads\<VERSION>\config\polymarket_csv
```

You must report the exact verified path for:

- the trade CSV
- the active wallets CSV

## Create the `.env` file

Create `.env` next to `sync_script.py` at:

```text
C:\Users\Administrator\wallet-curator\.env
```

Use this template:

```env
DATABASE_URL=postgresql://...
SHARP_DOWNLOADS_DIR=C:\Users\Administrator\Downloads
SHARP_CSV_FILENAME=polymarket_copytrade.csv
SHARP_CSV_SUBPATH=config\trade_result_csv
SHARP_WALLETS_FILENAME=active_wallets.csv
SHARP_WALLETS_SUBPATH=config\polymarket_csv
SYNC_INTERVAL_SECONDS=300
WALLET_SYNC_INTERVAL_SECONDS=3600
```

Important:

- do not finalize `DATABASE_URL` until you have the real value
- do not finalize `SHARP_WALLETS_SUBPATH` until you have verified the real wallet CSV path on disk

## Run the script

From the repo root, run:

```powershell
cd C:\Users\Administrator\wallet-curator
python sync_script.py
```

Watch the first sync cycle.

Confirm there is no immediate:

- import error
- filesystem error
- database connection error
- missing file error

## What to verify in logs

The first run should tell you things like:

- which Sharp version folder it is reading from
- whether it synced trades
- whether it synced active wallets
- whether it updated `sync_status`
- whether it dropped a partial final CSV line
- whether there was any DB connection issue

## Success criteria

You are done only when all of these are true:

- `sync_script.py` exists on the VPS
- repo is cloned in `C:\Users\Administrator\wallet-curator`
- current branch is `cloud-dashboard`
- Python 3.10+ is confirmed
- `psycopg2-binary` is installed
- `.env` exists next to `sync_script.py`
- `DATABASE_URL` is set correctly
- exact trade CSV path is verified
- exact active wallets CSV path is verified
- the script starts successfully
- at least one sync cycle completes without crashing

## Required final report

When finished, respond with:

1. where `sync_script.py` came from
   - cloned repo / existing repo
2. exact repo folder used
3. active Git branch name
4. exact folder where `sync_script.py` is located
5. Python version
6. exact verified trade CSV path
7. exact verified active wallets CSV path
8. whether `.env` was created
9. whether the script started successfully
10. the most important first-cycle log lines
11. any remaining blocker

## Boundaries

- Do not make unrelated repo changes.
- Do not open network ports.
- Do not assume the wallet CSV path.
- Do not invent missing files.
- Use the real repo and the checked-in `sync_script.py`.
```

## Local Note

If the VPS Codex session asks for `DATABASE_URL`, provide the exact Railway Postgres `DATABASE_URL`, not `DATABASE_PUBLIC_URL`.
