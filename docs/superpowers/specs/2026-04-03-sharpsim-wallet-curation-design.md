# Sharpsim Upload And Overlay For Wallet Curation

Date: 2026-04-03
Status: Approved design, ready for implementation planning

## Goal

Extend the Wallet Curation swipe-review flow so an uploaded Sharpsim workbook can:

- auto-populate the review queue and per-wallet filter scope
- overlay simulated copy-trade performance on top of actual wallet P&L
- show actual versus simulated summary stats during review
- export final review decisions with both actual and simulated outcome fields

The feature is meant to answer a practical curation question: "What would copying this wallet have earned me?" without replacing the existing actual-data review path.

## Scope

In scope:

- `.xlsx` upload from the Wallet Curation setup screen
- parsing the Sharpsim workbook into structured wallet and DRL trade data
- replacing manual wallet/category setup for the current tab when a valid workbook is loaded
- per-wallet filter switching based on workbook metadata
- simulated equity curve replay from DRL rows using the same price basis as the current wallet chart
- actual and simulated comparison stats during swipe review
- graceful per-wallet sim failure handling
- CSV export of review decisions and sim fields at session end

Out of scope:

- persistence across refresh, tabs, or server restarts
- editing uploaded workbook data in the UI
- background ingestion of Sharpsim files into a durable database
- replacing the existing manual review flow

## Final Decisions

- Persistence model: tab-only review state. Uploaded Sharpsim data does not survive refresh or a new tab.
- Integration model: keep one curation flow and layer sim mode into it.
- Overlay default: on by default for each wallet while a valid sim session is active.
- Failure handling: if sim parsing or replay fails for one wallet, keep the wallet reviewable with actual data and an explicit sim-unavailable state.
- Workbook failures: invalid workbook shape keeps the page in manual mode and shows an inline alert.
- Prefetch model: actual wallet data warms using per-wallet filter configs from the workbook, not one shared filter for the full batch.
- Export format: CSV with review decisions plus actual and sim outcome columns.

## Current Codebase Constraints

The existing review flow already has the right backbone:

- `app.py` owns a single curation setup screen, swipe screen, and results screen
- `cur-view` is the render contract for the current wallet
- `lib/curation_prefetch.py` warms and caches actual wallet payloads
- `lib/clickhouse_charts.py` already defines the price semantics used for actual wallet equity curves:
  - forward-filled daily closes
  - resolution prices after market close
  - interval rebasing
  - opening-position handling for trades before the visible window

The design should reuse those pieces rather than create a second curation stack.

## Recommended Approach

Use the current wallet curation flow as the only review pipeline and add a thin Sharpsim session layer:

1. Parse the uploaded workbook once into a normalized server-side session object.
2. Store only lightweight tab state in Dash stores.
3. Reuse the existing prefetch manager for actual data, but warm it with per-wallet filter configs.
4. Keep `render_curation_wallet()` as the single render path and augment it with sim overlay and comparison widgets.
5. Extend the existing results/export flow instead of adding a separate Sharpsim results page.

This minimizes duplication, keeps manual review intact, and aligns with the decision not to persist uploads beyond the current tab session.

## Architecture

### 1. Sharpsim Parser Module

Create `lib/sharpsim_parser.py` to handle workbook-specific logic.

Responsibilities:

- parse workbook-level metadata from `Info` and `Portfolio`
- parse wallet ordering and summary fields from `Results`
- parse DRL rows from `*_DRL` sheets
- normalize wallet addresses, filter scope, and numeric fields
- expose a replay function that converts copied DRL rows into a daily sim equity series using shared pricing rules

This module should stay pure and testable. It should not touch Dash components or callback state directly.

### 2. Sharpsim Session Store

Add a small in-process store, implemented in Python rather than Dash JSON state, to hold the parsed workbook for the active upload.

Reason:

- the workbook contains large DRL payloads
- keeping raw DRL rows in `dcc.Store` would create unnecessary browser payload and callback churn
- a server-side session object mirrors the existing prefetch manager pattern and keeps the browser state small

The browser should only hold a session identifier and UI booleans.

### 3. Existing Prefetch Manager

Update `lib/curation_prefetch.py` so session warming accepts per-wallet configs:

- current shape: one wallet list plus one shared filter
- new shape: one ordered list of wallet configs, where each config contains `address`, `filter_level`, and `filter_value`

This keeps actual-data warming aligned with the workbook-driven review order and scope.

### 4. Dash UI Layer

`app.py` remains the orchestrator:

- upload callback starts or clears sim mode
- start-review callback chooses between manual inputs and workbook-derived wallets
- status and render callbacks keep driving the swipe screen
- results callback extends existing export behavior

No new page or parallel review mode is introduced.

## State Model

### Browser State

Add lightweight stores in `wallet_curation_layout()`:

- `cur-sim-session-id`: active Sharpsim session token for this tab
- `cur-sim-active`: whether the review is driven by an uploaded Sharpsim workbook
- `cur-sim-overlay-visible`: whether the purple overlay is shown for the current wallet

Behavior:

- new valid upload sets `cur-sim-active=True`
- new valid upload resets `cur-sim-overlay-visible=True`
- switching to manual clears the sim session token and sets `cur-sim-active=False`
- overlay visibility is a local view toggle, not a saved preference

### Server-Side Sim Session Shape

The Sharpsim session object should contain:

- `session_meta`
  - upload filename
  - capital
  - copy ratio if available
  - execution mode if available
- `wallet_order`
  - ordered wallet addresses from the Results sheet
- `wallets[address]`
  - `address`
  - `filter_level`
  - `filter_value`
  - `category`
  - `subcategory`
  - precomputed summary stats from Results
  - copied/skipped counts
  - validation values from workbook
  - `sim_status`
  - `sim_error`
- `drl[address]`
  - normalized DRL trade rows for that wallet
- `filter_summary`
  - counts grouped by filter value for setup-screen summary text
- `parse_errors`
  - workbook-level issues that block sim mode

`sim_status` should be explicit and machine-readable. Expected values:

- `ready`
- `missing_drl`
- `replay_error`
- `parse_error`

## Workbook Parsing Rules

The workbook fixture at `tests/Sharpsim.xlsx` shows the relevant shape:

- `📊 Results` contains wallet ordering and summary fields
- `📦 Portfolio` contains capital inputs
- each `*_DRL` sheet contains copied and skipped replay rows

Parsing requirements:

- accept only `.xlsx`
- preserve wallet order from `Results`
- normalize wallet addresses to lowercase
- map `Detail` to `filter_value`
- use `filter_level="detail"` for this workbook format
- capture copied and skipped counts from the workbook
- parse `Market ID` as `condition_id`
- parse `Token ID`, timestamp, side, status, copied price, copied shares, and copied notional fields
- tolerate extra workbook columns without failing
- fail only when required sheets or required columns are missing

Workbook validation should reject sim mode when:

- `Results` is missing
- no valid wallet rows are found
- required wallet identity or scope columns are missing
- the workbook cannot be decoded as Excel

## Data Flow

### Upload Flow

1. User clicks `Upload Sharpsim` on the Wallet Curation setup screen.
2. The upload callback decodes the file and parses it into a Sharpsim session object.
3. If parsing fails:
   - show inline alert
   - keep manual textarea and category dropdown visible
   - do not activate sim mode
4. If parsing succeeds:
   - create a sim session entry in the server-side store
   - set `cur-sim-session-id`
   - set `cur-sim-active=True`
   - set `cur-sim-overlay-visible=True`
   - set `cur-wallets` from workbook order when review starts
   - hide manual setup controls
   - show a compact summary like wallet count plus filter distribution

### Start Review Flow

When sim mode is active:

- ignore manual wallet textarea and category dropdown values
- derive wallet configs from the Sharpsim session object
- prime actual-data prefetch with ordered per-wallet filter configs

When sim mode is inactive:

- preserve today’s manual start behavior

### Swipe Render Flow

For each wallet:

1. Read actual payload from `CurationPrefetchManager`.
2. Read sim wallet metadata and DRL rows from the Sharpsim session store.
3. If sim wallet data is `ready`, build the replay series for the selected interval.
4. Render the actual chart as today.
5. If the overlay toggle is on and sim replay succeeded, add the purple sim trace.
6. Render paired actual-versus-sim stats plus a sim summary strip.
7. If sim replay is unavailable, keep the actual view and render a compact warning instead of sim widgets.

## Sim Replay Rules

The sim series must use the same price basis as the actual curation chart to make the overlay defensible.

Rules:

- replay only `COPIED` DRL rows
- ignore `SKIPPED` rows for PnL construction, but still surface copied/skipped counts in UI
- track opening positions before the visible interval so rebasing works correctly
- use forward-filled `token_daily_close` for active markets
- use resolution prices once the market is resolved
- compute the interval window the same way as the actual curation chart
- rebase the sim series to zero at the interval start

The replay function should reuse or mirror the pricing helpers in `lib/clickhouse_charts.py` rather than define a second set of pricing semantics.

## UI Design

### Setup Screen

Add:

- `Upload Sharpsim` secondary button
- inline summary of loaded workbook state
- `Switch to manual` button when sim mode is active

Behavior:

- valid upload hides the manual wallet textarea and category dropdown
- setup area displays a compact loaded-state summary such as wallet count and filter mix
- switching to manual clears only the active sim session for the current tab and restores the existing setup controls

### Swipe Screen

Keep the existing structure and add only three sim-specific elements:

- a default-on show or hide control for the sim overlay
- a sim summary strip
- paired actual and sim stat presentation, including a validation row

Sim summary strip content:

- capital
- copy ratio if available
- execution mode if available
- copied and skipped counts

Comparison stats:

- actual final PnL versus sim final PnL
- actual ROI versus sim ROI
- trade counts and copied counts as separate measures
- workbook validation values versus recomputed replay values for quick confidence checks

Failure UI for a wallet with no usable sim data:

- keep the actual chart and actual stats
- show one inline warning explaining that sim data is unavailable for this wallet
- render sim values as `N/A`
- keep swipe controls unchanged

### Results Screen And Export

Extend the existing results screen rather than replacing it.

Changes:

- keep the approved summary at session end
- replace or extend the current text download with CSV export
- include one row per reviewed wallet, not just approved wallets

CSV columns:

- `wallet`
- `filter_level`
- `filter_value`
- `decision`
- `actual_final_pnl`
- `actual_roi_pct`
- `sim_final_pnl`
- `sim_roi_pct`
- `sim_copied`
- `sim_skipped`
- `sim_status`

## Failure Handling

### Workbook-Level Failures

If the upload is not a valid Sharpsim workbook:

- show an inline alert on the setup screen
- leave the page in manual mode
- do not store a broken sim session

### Wallet-Level Sim Failures

A wallet stays reviewable even if sim data is incomplete or replay fails.

Expected wallet-level cases:

- DRL sheet missing for one wallet
- DRL rows present but malformed
- replay cannot reconcile required price inputs

Behavior:

- actual review path still renders
- sim widgets degrade to warning plus `N/A`
- export records the failure via `sim_status`

### Actual Data Failures

Existing prefetch and render error behavior for actual ClickHouse data should remain unchanged and independent from sim mode.

## Testing Strategy

### Parser Tests

Create `tests/test_sharpsim_parser.py` with coverage for:

- parsing the real `tests/Sharpsim.xlsx` fixture
- required sheet and column validation
- wallet order preservation
- lowercase wallet normalization
- extracted copied and skipped counts
- malformed workbook handling

### Replay Tests

Add coverage for:

- copied-only filtering
- interval rebasing
- opening-position handling before the visible window
- resolution pricing takeover after market close
- deterministic comparison between precomputed workbook values and recomputed replay values where applicable

### Prefetch Tests

Update `tests/test_curation_prefetch.py` so session priming uses wallet configs with per-wallet filters rather than one shared filter input.

### App Callback Tests

Extend `tests/test_app_curation.py` for:

- successful upload switches the setup screen into sim mode
- invalid upload leaves the setup screen in manual mode
- start-review uses workbook wallets and per-wallet filters when sim mode is active
- degraded-wallet render path keeps actual data visible when sim data is unavailable
- CSV export includes sim columns and `sim_status`

## Files To Change

- `lib/sharpsim_parser.py`
  - new parser and replay helpers
- `lib/curation_prefetch.py`
  - accept wallet configs with per-wallet filter scope
- `app.py`
  - upload UI, sim state, mode switching, overlay rendering, results export
- `assets/dashboard-ui.css`
  - styling for sim summary strip, overlay control, and comparison stats
- `tests/test_sharpsim_parser.py`
  - new parser and replay tests
- `tests/test_curation_prefetch.py`
  - updated prefetch contract coverage
- `tests/test_app_curation.py`
  - callback and CSV export coverage

## Implementation Notes

- Do not store raw DRL workbook payloads inside `dcc.Store`.
- Do not build a second swipe-review page or callback stack for Sharpsim mode.
- Keep manual mode fully functional behind the same setup screen.
- Reuse the existing chart pricing semantics for the sim overlay so the comparison is internally consistent.
- Keep failure states explicit and exportable rather than silently dropping wallets.

## Acceptance Criteria

- A valid Sharpsim workbook can replace manual setup for the current tab.
- Review starts in workbook order with workbook-defined per-wallet filter scope.
- The swipe chart shows actual P&L and a default-on purple sim overlay when replay succeeds.
- Interval switches re-scope and rebase both series consistently.
- Wallets with sim issues remain reviewable with an explicit degraded sim state.
- The results download exports a CSV with decision, actual outcome fields, sim outcome fields, and `sim_status`.
- An invalid workbook does not break manual review mode.
