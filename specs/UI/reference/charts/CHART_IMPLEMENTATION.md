# Subcategory Chart — Implementation Guide

## What It Does

Given a wallet address and a filter (category, subcategory, or detail), this builds a **daily marked-to-market P&L chart** from ClickHouse data. The chart shows how a wallet's positions in that scope performed over time, including unrealized gains/losses from live token prices and resolved market settlements.

## Architecture

```
User inputs wallet + filter + lookback
        ↓
Python backend (lib/clickhouse_charts.py)
        ↓
4 ClickHouse queries:
  1. Token scope → which tokens did this wallet trade in this filter?
  2. Raw trades → every buy/sell with shares, USDC, fees
  3. Daily closes → end-of-day token prices
  4. Resolution prices → settlement prices for closed markets
        ↓
Python reconstructs daily P&L series
        ↓
Returns JSON → frontend renders Plotly chart
```

## Filter Levels

The dropdown supports 3 levels. The value is encoded as `"level::value"`:

| Level | Example Value | What It Matches | ClickHouse Column |
|-------|--------------|-----------------|-------------------|
| `category` | `category::Sports` | ALL sports markets | `token_metadata_latest_v2.category` |
| `subcategory` | `subcategory::Tennis` | All tennis (ATP, WTA, Grand Slams) | `token_metadata_latest_v2.subcategory` |
| `detail` | `detail::Counter-Strike` | Only Counter-Strike markets | `token_metadata_latest_v2.subcategory_detail` |

## Backend Entry Point

**File:** `lib/clickhouse_charts.py`

```python
from lib.clickhouse_charts import get_wallet_game_chart

payload = get_wallet_game_chart(
    wallet="0x4762c329459b5bbf87e9fc9f65749efc7086ba70",
    filter_value="Valorant",
    lookback_days=365,
    filter_level="detail",  # "category", "subcategory", or "detail"
)
```

Returns `None` if no trades found, otherwise returns a dict.

## Response Shape

```json
{
  "meta": {
    "wallet": "0x...",
    "game": "Valorant",
    "lookback_days": 365
  },
  "summary": {
    "first_trade_date": "2025-09-25",
    "last_trade_date": "2026-03-26",
    "chart_end_date": "2026-03-30",
    "total_trades": 4567,
    "scoped_tokens": 224,
    "scoped_conditions": 87,
    "final_pnl": 377556.03,
    "max_drawdown_pct": 452.56,
    "total_volume_usd": 1382806.54
  },
  "series": [
    {
      "date": "2025-09-25",
      "pnl": -24276.36,
      "cumulative_cash": 978.99,
      "marked_value": -25255.36,
      "daily_trade_count": 20
    },
    ...
  ]
}
```

### Field Definitions

**summary:**
- `first_trade_date` / `last_trade_date` — date range of actual trades
- `chart_end_date` — extends to today or latest close price date
- `total_trades` — raw trade count in scope
- `scoped_tokens` — unique token IDs traded
- `scoped_conditions` — unique market conditions (one market can have 2 tokens: YES/NO)
- `final_pnl` — last day's P&L value (the headline number)
- `max_drawdown_pct` — largest peak-to-trough drop as percentage
- `total_volume_usd` — sum of all USDC traded

**series (one entry per day):**
- `date` — ISO date string
- `pnl` — `cumulative_cash + marked_value` (the chart line)
- `cumulative_cash` — running total of cash in/out from trades minus fees
- `marked_value` — current value of all open positions at that day's prices
- `daily_trade_count` — trades executed on that day

## P&L Reconstruction Logic

For each trade:
- `BUY`: shares go up, cash goes down (`-usdc - fee_usdc`)
- `SELL`: shares go down, cash goes up (`+usdc - fee_usdc`)

For each day in the calendar:
1. Update cumulative share positions per token
2. Update cumulative cash balance
3. Price each token position using:
   - `token_daily_close.close_price` while market is active (forward-filled if no data for a day)
   - `market_resolution.token_prices[token_id]` once market is closed (`closed = 1`)
4. `marked_value = sum(position[token] × price[token])` across all tokens
5. `pnl = cumulative_cash + marked_value`

## ClickHouse Queries

### 1. Token Scope
Find which tokens this wallet traded for the selected filter:

```sql
SELECT t.token_id, any(t.condition_id) AS condition_id, any(tm.question) AS question,
       min(t.ts) AS first_trade_ts, max(t.ts) AS last_trade_ts
FROM polymarket.trades AS t
INNER JOIN polymarket.token_metadata_latest_v2 AS tm ON tm.token_id = t.token_id
WHERE t.wallet = '0x...'
  AND tm.subcategory_detail = 'Valorant'  -- or tm.subcategory or tm.category depending on level
  AND t.ts >= now() - INTERVAL 365 DAY
GROUP BY t.token_id
ORDER BY first_trade_ts ASC
```

### 2. Raw Trades
```sql
SELECT toDate(ts) AS trade_date, ts, token_id, condition_id, side,
       toFloat64(shares) AS shares, toFloat64(usdc) AS usdc,
       toFloat64(fee_usdc) AS fee_usdc, toFloat64(price) AS price, role
FROM polymarket.trades
WHERE wallet = '0x...' AND token_id IN (...) AND ts >= now() - INTERVAL 365 DAY
ORDER BY ts ASC
```

### 3. Daily Close Prices
```sql
SELECT token_id, trade_date, toFloat64(close_price) AS close_price, close_ts
FROM polymarket.token_daily_close
WHERE token_id IN (...)
ORDER BY token_id ASC, trade_date ASC
```

### 4. Resolution Prices
```sql
SELECT tm.token_id, tm.condition_id, mr.closed, mr.resolved_ts, mr.updated_ts, mr.token_prices
FROM polymarket.token_metadata_latest_v2 AS tm
INNER JOIN polymarket.market_resolution AS mr ON mr.condition_id = tm.condition_id
WHERE tm.token_id IN (...)
ORDER BY tm.token_id ASC, updated_ts DESC
```

Only use rows where `closed = 1` and `token_prices[token_id]` exists.

## ClickHouse Tables Used

| Table | Purpose |
|-------|---------|
| `polymarket.trades` | Raw buy/sell trades with wallet, token, shares, USDC, fees |
| `polymarket.token_metadata_latest_v2` | Token → category/subcategory/detail mapping, question text |
| `polymarket.token_daily_close` | End-of-day token prices |
| `polymarket.market_resolution` | Settlement data for closed markets |

## Available Filter Options

Query to get all available dropdown options:

```sql
-- Categories (broadest)
SELECT category AS label, count() AS token_count
FROM polymarket.token_metadata_latest_v2
WHERE category != '' GROUP BY category ORDER BY token_count DESC

-- Subcategories (medium)
SELECT subcategory AS label, count() AS token_count
FROM polymarket.token_metadata_latest_v2
WHERE subcategory != '' GROUP BY subcategory ORDER BY token_count DESC

-- Details (most specific)
SELECT subcategory_detail AS label, count() AS token_count
FROM polymarket.token_metadata_latest_v2
WHERE subcategory_detail != '' GROUP BY subcategory_detail ORDER BY token_count DESC
```

Current counts: 8 categories, ~20 subcategories, ~190 details = ~218 total filter options.

## Environment Variables

```env
CLICKHOUSE_URL=http://127.0.0.1:8123/      # or direct Hetzner IP for production
CLICKHOUSE_USER=jake
CLICKHOUSE_PASSWORD=<password>
CLICKHOUSE_DATABASE=polymarket
```

Local dev uses SSH tunnel: `ssh -N -L 8123:127.0.0.1:8123 -i ~/.ssh/jake_hetzner_ed25519 jake@142.132.139.47`

## Frontend Chart Rendering

The current Dash implementation uses Plotly:

```python
fig = go.Figure()
fig.add_trace(go.Scatter(
    x=[s["date"] for s in series],
    y=[s["pnl"] for s in series],
    mode="lines",
    line=dict(color="#22c55e" if final_pnl >= 0 else "#ef4444", width=2),
    fill="tozeroy",
    fillcolor="rgba(34,197,94,0.12)" if final_pnl >= 0 else "rgba(239,68,68,0.12)",
    hovertemplate=(
        "<b>%{x}</b><br>"
        "P&L: $%{y:,.2f}<br>"
        "Cash: $%{customdata[0]:,.2f}<br>"
        "Marked Value: $%{customdata[1]:,.2f}<br>"
        "Trades: %{customdata[2]}<extra></extra>"
    ),
))
```

For a non-Dash frontend, use any chart library (Plotly.js, Recharts, ECharts, lightweight-charts) and render the `series` array as a line chart. The `pnl` field is the Y-axis value.

## API Endpoint (if building a REST API)

```
GET /api/wallet-chart?wallet=0x...&filter=subcategory::Tennis&lookback=365
```

Response: the JSON payload described above.

## Caching

Recommended cache key: `wallet + filter_level + filter_value + lookback_days`

TTL:
- Active markets (recent trades): 5-15 minutes
- Fully historical: 1-24 hours

## File Reference

| File | Purpose |
|------|---------|
| `lib/clickhouse_charts.py` | All ClickHouse queries + P&L reconstruction logic |
| `app.py` (subcategory_charts_layout + callbacks) | Dash page layout + generate chart callback |
| `specs/WALLET_GAME_CHART_INTEGRATION.md` | Original spec from your dev |
| `specs/CHART_IMPLEMENTATION.md` | This file |
