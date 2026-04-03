# Game-Specific Wallet PnL Chart Integration Guide

## What You Are Building

This feature creates a **Polymarket-style PnL chart for a single wallet scoped to a single game**.

Example:
- wallet: `0x4762c329459b5bbf87e9fc9f65749efc7086ba70`
- game: `Valorant`

The chart is **daily marked-to-market PnL**, not just realized PnL.

That means the line is built from:
- raw wallet trades
- daily token close prices
- resolution settlement prices for closed markets

The resulting curve is much closer to what Polymarket shows than a simple buy/sell cashflow line.

## Important Constraint: Your App Does Not Call MCP Directly

The ClickHouse MCP server is available to Codex inside this assistant environment.
Your application page should **not** try to call MCP directly from the browser.

For your app, the correct architecture is:

1. Frontend page requests chart data from your own backend.
2. Your backend queries ClickHouse directly.
3. Backend returns a clean JSON time series.
4. Frontend renders the chart.

Treat MCP as:
- a development/debugging tool for Codex
- not as your production app transport layer

## Recommended Architecture

Use this structure:

```text
Frontend Page
  -> GET /api/wallet-game-chart?wallet=...&game=Valorant&lookbackDays=365
Backend Route
  -> query ClickHouse
  -> reconstruct daily marked-to-market series
  -> return JSON
Frontend Chart Component
  -> render JSON as Polymarket-style line chart
```

If you already have a backend, add the chart there.
Do not put ClickHouse credentials in the frontend.

## Data Sources

Use these tables:

- `polymarket.trades`
- `polymarket.token_metadata_latest_v2`
- `polymarket.token_daily_close`
- `polymarket.market_resolution`

Use game scope from:
- `token_metadata_latest_v2.subcategory_detail`

For example:
- `Valorant`
- `League of Legends`
- `Counter-Strike`

## Chart Definition

Build a **daily marked-to-market PnL series**.

For each day:

1. Sum all wallet cashflow from trades:
   - `BUY` = negative cash
   - `SELL` = positive cash
   - subtract fees on both sides
2. Track cumulative token position by day.
3. Price each token position using:
   - `token_daily_close.close_price` while market is active
   - `market_resolution.token_prices[token_id]` once `closed = 1`
4. Compute:
   - `pnl = cumulative_cash + marked_value`

This is the same logic implemented in:
- [wallet_game_chart.py](/Users/jakemilken/Desktop/Manual%20Library/Clickhouse-Walletfinder-MCP/wallet_game_chart.py)

## Backend Implementation

### Step 1: Build a Route

Create a backend endpoint like:

```text
GET /api/wallet-game-chart?wallet=0x...&game=Valorant&lookbackDays=365
```

Return JSON like:

```json
{
  "meta": {
    "wallet": "0x...",
    "game": "Valorant",
    "pricing_mode": "daily_close_plus_resolution",
    "lookback_days": 365
  },
  "summary": {
    "first_trade_date": "2025-09-25",
    "last_trade_date": "2026-03-26",
    "chart_end_date": "2026-03-29",
    "total_trades": 4567,
    "scoped_tokens": 224,
    "scoped_conditions": 87,
    "final_pnl": 377556.029618,
    "max_drawdown_pct": 452.560593,
    "total_volume_usd": 1382806.543799
  },
  "series": [
    {
      "date": "2025-09-25",
      "pnl": -24276.362996,
      "cumulative_cash": 978.999764,
      "marked_value": -25255.36276,
      "daily_trade_count": 20
    }
  ]
}
```

### Step 2: Query Token Scope

First find the tokens this wallet traded for the specific game:

```sql
SELECT
    t.token_id AS token_id,
    any(t.condition_id) AS condition_id,
    any(tm.question) AS question,
    min(t.ts) AS first_trade_ts,
    max(t.ts) AS last_trade_ts
FROM polymarket.trades AS t
INNER JOIN polymarket.token_metadata_latest_v2 AS tm
    ON tm.token_id = t.token_id
WHERE t.wallet = {wallet}
  AND tm.subcategory_detail = {game}
  AND t.ts >= now() - INTERVAL {lookback_days} DAY
GROUP BY t.token_id
ORDER BY first_trade_ts ASC, token_id ASC
```

### Step 3: Query Raw Trades

```sql
SELECT
    toDate(ts) AS trade_date,
    ts,
    token_id,
    condition_id,
    side,
    toFloat64(shares) AS shares,
    toFloat64(usdc) AS usdc,
    toFloat64(fee_usdc) AS fee_usdc,
    toFloat64(price) AS price,
    role
FROM polymarket.trades
WHERE wallet = {wallet}
  AND token_id IN ({token_ids})
  AND ts >= now() - INTERVAL {lookback_days} DAY
ORDER BY ts ASC, token_id ASC
```

### Step 4: Query Daily Closes

```sql
SELECT
    token_id,
    trade_date,
    toFloat64(close_price) AS close_price,
    close_ts
FROM polymarket.token_daily_close
WHERE token_id IN ({token_ids})
ORDER BY token_id ASC, trade_date ASC
```

### Step 5: Query Resolution Prices

```sql
SELECT
    tm.token_id AS token_id,
    tm.condition_id AS condition_id,
    mr.closed AS closed,
    mr.resolved_ts AS resolved_ts,
    mr.updated_ts AS updated_ts,
    mr.token_prices AS token_prices
FROM polymarket.token_metadata_latest_v2 AS tm
INNER JOIN polymarket.market_resolution AS mr
    ON mr.condition_id = tm.condition_id
WHERE tm.token_id IN ({token_ids})
ORDER BY tm.token_id ASC, updated_ts DESC
```

Use only rows where:
- `closed = 1`
- `token_prices[token_id]` exists

## PnL Reconstruction Logic

Implement this on the backend in application code, not in one huge SQL query.

For each trade:

- `shares_delta = +shares` if `side = 'BUY'`
- `shares_delta = -shares` if `side = 'SELL'`
- `cash_delta = -usdc` if `side = 'BUY'`
- `cash_delta = +usdc` if `side = 'SELL'`
- always subtract `fee_usdc`

Then:

1. Build a full daily calendar from first scoped trade date to chart end date.
2. Aggregate daily share deltas by `token_id`.
3. Aggregate daily cash deltas at wallet level.
4. Compute cumulative positions for each token.
5. Build a daily price series for each token:
   - use `token_daily_close` while active
   - forward-fill the last known close if a day has no explicit close row
   - once the market is resolved and `closed = 1`, use settlement price from `market_resolution.token_prices[token_id]`
6. For each day:
   - `marked_value = sum(position[token] * price[token])`
   - `pnl = cumulative_cash + marked_value`

## Frontend Implementation

### Recommended Flow

Frontend should:

1. call your backend endpoint
2. receive `summary + series`
3. render:
   - headline PnL
   - date range
   - number of trades
   - volume
   - main line chart

### Chart Libraries

Use one of:

- Plotly
- Recharts
- ECharts

If you want the quickest match to the prototype here, use Plotly.

### Suggested UI

Show:

- title: `Valorant Wallet Performance`
- wallet address
- total PnL
- trade count
- scoped volume
- max drawdown
- line chart with range buttons:
  - `30D`
  - `90D`
  - `ALL`

Hover should show:

- date
- PnL
- cumulative cash
- marked value
- daily trade count

## Where To Put Credentials

### For Your Actual App

Put ClickHouse credentials in **backend-only environment variables**.

Do not put them:

- in frontend code
- in client-side `.env`
- in committed source files

Recommended backend env vars:

```env
CLICKHOUSE_URL=http://127.0.0.1:8123/
CLICKHOUSE_USER=jake
CLICKHOUSE_PASSWORD=your_password_here
CLICKHOUSE_DATABASE=polymarket
```

If you use an SSH tunnel locally, also make sure the tunnel is running:

```bash
ssh -N \
  -L 8123:127.0.0.1:8123 \
  -L 9000:127.0.0.1:9000 \
  -i ~/.ssh/jake_hetzner_ed25519 \
  jake@142.132.139.47
```

Then your backend can hit:

```text
http://127.0.0.1:8123/
```

### For Codex / Local Assistant Tooling

In this workspace, Codex MCP uses:

- file: `~/.codex/config.toml`
- section: `[mcp_servers.polymarket-clickhouse.env]`

Example:

```toml
[mcp_servers.polymarket-clickhouse.env]
CLICKHOUSE_HOST = "127.0.0.1"
CLICKHOUSE_PORT = "8123"
CLICKHOUSE_USER = "jake"
CLICKHOUSE_PASSWORD = "your_password_here"
CLICKHOUSE_SECURE = "false"
```

This is why MCP worked in assistant tooling while your raw local script originally failed:
- MCP had access to `~/.codex/config.toml`
- your shell process did not

### Recommended Production Setup

For a real app:

1. Store credentials in server env vars or your deployment secret manager.
2. Keep Codex MCP config separate from app runtime config.
3. Do not rely on `~/.codex/config.toml` in production.

Use `~/.codex/config.toml` only as a local fallback for Codex-driven tooling if you want convenience.

## Credential Loading Recommendation

Use this priority order in your app/backend:

1. explicit config passed by deployment
2. environment variables
3. optional local dev fallback file

Do not reverse that order.

The safe default is:
- production/staging uses env vars or secret manager
- local dev can optionally read a local config file

## How To Reuse The Existing Script

If you want the fastest path, you already have a working reference implementation:

- [wallet_game_chart.py](/Users/jakemilken/Desktop/Manual%20Library/Clickhouse-Walletfinder-MCP/wallet_game_chart.py)

You can reuse it in two ways:

### Option 1: Copy the core logic into your backend

Best choice if:
- you want a normal app endpoint
- you want to control caching, auth, and UI responses

Reuse these pieces:

- ClickHouse client
- token scope query
- trades query
- daily close query
- resolution query
- `build_payload`

### Option 2: Wrap the script from your backend

Best only for quick prototypes.

Flow:

1. backend runs the script as a subprocess
2. script writes JSON
3. backend reads JSON and returns it

This is workable, but it is not the clean long-term design.

## Suggested Backend API Contract

Request:

```http
GET /api/wallet-game-chart?wallet=0x4762c329459b5bbf87e9fc9f65749efc7086ba70&game=Valorant&lookbackDays=365
```

Validation:

- require `wallet`
- require `game`
- default `lookbackDays = 365`
- cap `lookbackDays` to something reasonable like `730`

Errors:

- `400` invalid input
- `404` no scoped trades found
- `500` ClickHouse or reconstruction failure

## Caching

You should cache these responses.

Good cache key:

```text
wallet + game + lookbackDays
```

Suggested TTL:

- active markets: `5-15 minutes`
- fully resolved historical-only sets: `1-24 hours`

This will keep the page fast and reduce ClickHouse load.

## Smoothness Ranking Later

You mentioned that smoother PnL curves tend to copy better.

This chart feature is the correct foundation for that.

Once the JSON series exists, you can later score:

- max drawdown
- number of large day-to-day jumps
- rolling volatility
- percent of positive days
- ratio of final PnL to realized variance

Do not mix that into the first chart implementation.
First get:

- correct daily series
- correct game scoping
- correct resolution pricing

Then layer smoothness scoring on top.

## What To Put In The New Project

If you are adding this to another app, the minimum pieces are:

1. backend chart service
2. backend ClickHouse config
3. `/api/wallet-game-chart` route
4. frontend chart component
5. loading/error states
6. optional cache

## Recommended File Layout

If the new project is a typical web app, use something like:

```text
backend/
  services/walletGameChart.ts
  routes/walletGameChart.ts
  lib/clickhouse.ts

frontend/
  components/WalletGameChart.tsx
  pages/WalletProfile.tsx
```

Or for Next.js:

```text
app/api/wallet-game-chart/route.ts
lib/clickhouse.ts
lib/wallet-game-chart.ts
components/wallet-game-chart.tsx
```

## Security Notes

Never commit:

- real ClickHouse passwords
- tunnel credentials
- `.env` files with production secrets

Do:

- use env vars
- use secret manager in deployment
- keep backend-only access

Do not:

- expose ClickHouse directly to the browser
- let frontend call your tunnel endpoint
- store credentials in client bundles

## Local Dev Checklist

1. start the SSH tunnel
2. confirm ClickHouse responds locally
3. set backend env vars
4. hit your backend route
5. verify the JSON shape
6. render the frontend chart

Quick connectivity test:

```bash
curl -u "jake:$CLICKHOUSE_PASSWORD" "http://127.0.0.1:8123/?query=SELECT%201"
```

Expected response:

```text
1
```

## Common Failure Modes

### `AUTHENTICATION_FAILED`

Cause:
- password missing or wrong

Fix:
- set `CLICKHOUSE_PASSWORD`
- verify tunnel is pointing to the intended server

### Empty Chart

Cause:
- wrong `subcategory_detail`
- wallet has no trades for that game

Fix:
- check the wallet/game scope query first

### Weird Future Pricing

Cause:
- using `market_resolution` rows that are not actually closed

Fix:
- only apply settlement prices when `closed = 1`

### Browser Can’t Load Chart

Cause:
- frontend trying to call ClickHouse directly

Fix:
- route through your backend

## Final Recommendation

For the other project, do this:

1. copy the backend logic from [wallet_game_chart.py](/Users/jakemilken/Desktop/Manual%20Library/Clickhouse-Walletfinder-MCP/wallet_game_chart.py)
2. put ClickHouse credentials in backend env vars
3. expose a JSON endpoint
4. render the JSON on the page with Plotly or your preferred chart library
5. add caching

That is the clean implementation path.

If you want, the next step I can do is write a second project-specific guide for:
- Next.js
- React + Express
- Rails
- Python/FastAPI

If you tell me the stack, I can make the exact file-by-file implementation doc for that project.
