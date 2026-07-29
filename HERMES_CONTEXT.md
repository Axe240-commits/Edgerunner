# HERMES CONTEXT — Edgerunner

Handover for agents (Hermes and others) working with this system.
Status: 2026-07-29. Owner: Albert. Built with Kimi; reviewed by Codex.

## 1. What this system is

BTC trading-research engine. Goal (from AGENTS.md): a market-state engine
that describes every candle completely, reconstructs liquidity/seeker
cycles, and turns robust state transitions into tradeable logic — NOT a
bag of indicator signals.

Four layers: Raw Market Data → Candle Features (one row per candle per TF,
all truth lives here first) → Cycle/Context (seeker cycles, zones) →
Research/Execution (transition families, validation, paper trading).

## 2. Current state (honest)

**Research verdict: no tradeable edge found yet.** 8 strategy cards were
tested against 18 months of BTC (Binance Futures, real delta) with strict
methodology. All dead. Full scoreboard: `RESEARCH_FINDINGS.md`.
Forensic evidence per run: `evidence/*.json` (DB hash, adapter hashes,
coverage/gaps, exact command, result hash — committed).

The three laws the research established:
1. **Cost-R scaling**: costs in R ∝ entry/risk. 10 bps eats 0.65–0.8R at
   tight stops (M1/H1), only 0.16R at H4 stops. Tight-stop strategies
   fight a wall before any edge counts.
2. **0R convergence**: with costs under ~0.2R, ALL price-based variants
   (fade AND follow, BOS AND seeker-kill) land at 0R ± 0.1R gross on H4.
   BTC is two-sided efficient after H4 structure breaks.
3. **Reclaim rate 80–88%**: most H-structure breaks get reclaimed
   (H1 88.4%, H4 80.9%, median 5 bars). Real, stable — but priced in,
   not monetizable from price data alone.

**The one living hint:** non-price selection. Funding-rate conditioning
lifted the H4 flip by +0.1R (card 8). Whale+funding combined selection
is the designated next test, once whale history matures (~Oct 2026).

## 3. Key components

- `candle_analyzer.py` — per-candle feature generation (89 features:
  anatomy, BOS/CHoCH, break quality, seeker flags/zones, MACD+div,
  volume/delta, context). Input: timestamp/OHLC/volume/delta.
- `db.py` — SQLite persistence, `candles_<tf>` tables, FEATURE_COLUMNS
  (115 cols), auth tables.
- `seeker_cycles.py` — cycle reconstruction (origin/div/kill events,
  zone geometry, open/killed status).
- `hyperliquid_api.py` — data sources. `fetch_candles` (HL),
  `fetch_binance_futures_candles` (Binance: OHLCV + BTC volume + real
  taker delta, 10m aggregated from 5m with strict grid equality).
- `history_loader.py` — backfill. `--source {hyperliquid|binance-futures}`,
  `--days/--start/--end`, `--fill-gaps`, `--live-open-candle`.
  Source-specific start floors (HL 2023-04, Binance 2019-09).
  Status: ok / incomplete (source ended early) / failed (abort).
- `funding_loader.py` — Binance funding-rate backfill → separate
  `funding.db` (never into edgerunner.db).
- `backtest_{breaker,flip,seeker,trend}.py` — TF-generic backtest lab
  (point-in-time strict, costs, Wilson CIs, train/OOS split 65/35,
  diagnose/validate modes, evidence artifacts per run).
- `edgerunner_server.py` — dashboard/API server (has known security
  issues from early review; not part of the research path).
- `research_fakeout_reclaim.py` — legacy research runner (fixed for
  lookahead/costs/threshold-freezing; superseded by the backtest lab).

## 4. Data

- **Production DB**: `C:\edgerunner\edgerunner.db` on the Windows PC
  (192.168.0.199). 18 months from 2025-01, 12 timeframes:
  1m 792k / 3m 264k / 5m 158k / 10m 79.2k / 15m 52.8k / 30m 26.4k /
  1h 13.2k / 2h 6.6k / 4h 3.3k / 1d 550 / 1w 78 (+1M) candles.
  ~1 GB. Source: Binance Futures (BTCUSDT) with real delta + spot merge.
- **`funding.db`**: 1712 funding prints (8h cadence) since 2025-01.
- **Read-only rule**: research scripts open the DB `mode=ro` and never
  write to it. Backfills are deliberate maintenance tasks only.
- Timestamps: epoch ms (open-time). HTF data is usable only after the
  HTF candle CLOSES (`timestamp + tf_ms <= ts`) — point-in-time rule,
  enforced everywhere.

## 5. Methodology contract (how research is done here)

- One hypothesis per card, fixed parameters, kill criteria BEFORE the run.
- Costs: 5+5 bps default (fee+slippage per side), sensitivity 2+5/10+10.
- Train 65% / OOS 35%; validate only if train net expectancy > 0.
- Kill: OOS expectancy <= 0, n < 100, or Wilson-95% CI includes breakeven.
- Every run writes an immutable evidence artifact (`evidence/`).
- 64 tests guard the machinery (`test_*.py`, run via unittest).

## 6. Windows host layout

`C:\edgerunner` (git checkout, master). Research runs execute there
(Python 3.12 at `C:\Users\Home PC\AppData\Local\Programs\Python\Python312`).
Reports land in `C:\edgerunner\` and are copied back to
`~/edgerunner/.runtime/` (untracked) or `evidence/` (committed).

## 7. Related systems

- **shadow-tracker** (whale intelligence): the future signal source.
  See its HERMES_CONTEXT.md. Whale DB collects since 2026-07-24.
- Roadmap: paper-trading pipeline for the funding-flip card with whale
  overlay → Whale+Funding backtest (~Oct 2026) → recalibration loop
  (monthly, promotion gate) → small live only after paper phase.
- PyTorch scoring layer comes LAST (quality-scorer on top of rule
  candidates), only once logged outcomes exist.

## 8. Anti-patterns (learned the hard way)

- Do NOT resurrect dead cards by re-tuning: category is exhausted,
  documented, both directions.
- Do NOT trust any win rate without n and Wilson CI.
- Do NOT backtest whale features — whale history starts 2026-07-24.
- Do NOT mix HL and Binance price sources within one series.
- Do NOT let UI heuristics become source of truth (AGENTS.md rule 2).
