# Edgerunner Architecture Plan

This is the current architecture plan for Edgerunner.

It exists so the project can grow without collapsing into:

- duplicated interpretation
- stale heuristics
- UI-driven logic
- unowned subsystems

## 1. System Goal

Edgerunner should become a state-aware trading research engine.

It should answer:

- where the market is now
- which liquidity / seeker cycles are active
- which phase transition is likely next
- which execution models are robust for that transition

It should not become a bag of unrelated indicator signals.

## 2. Layered Architecture

### Layer A: Raw Data

Purpose:

- ingest and normalize source market data

Inputs:

- candles
- spot volume
- futures volume
- delta
- whale / liquidation overlays where available

Core files:

- `hyperliquid_api.py`
- `history_loader.py`
- `db.py`

Outputs:

- timeframe tables
- normalized OHLCV-aligned records

### Layer B: Candle Feature Layer

Purpose:

- describe each candle completely

Responsibilities:

- candle structure
- BOS / CHoCH
- swing context
- break counts
- break depth
- divergence
- seeker origin / div / kill flags
- volume and delta context
- candle shape
- HTF reference fields where already stored

Core files:

- `candle_analyzer.py`
- `db.py`

Outputs:

- `candles_*` tables containing full per-candle feature truth

Design rule:

- interpretation must not be used to compensate for missing candle fields

### Layer C: Seeker Cycle Layer

Purpose:

- convert candle seeker events into persistent cycle objects

Responsibilities:

- create cycle IDs
- store origin zone geometry
- store cycle status
- store cycle event history
- answer nearest open / killed zone queries

Core files:

- `seeker_cycles.py`
- `edgerunner_server.py`

Current data objects:

- `seeker_cycles`
- `seeker_cycle_events`

Current v1 status model:

- `open`
- `killed`

Planned next states:

- `under_attack`
- `retested`
- `reclaimed`
- `invalidated`

### Layer D: Context Engine

Purpose:

- build the market state around a given anchor candle

Responsibilities:

- collect aligned TF state
- summarize zone placement per TF
- summarize structure, div, seeker, flow, volume, and context
- provide the "what was true at time X" snapshot

Consumers:

- native inspector extensions
- team room case analysis
- live capture analysis
- research runners

Rule:

- context is built from candle + cycle truth
- it is not a separate invented data universe

### Layer E: Research / Transition Engine

Purpose:

- find robust, testable edge from state transitions

Responsibilities:

- label families such as:
  - `fakeout_to_reclaim`
  - `fakeout_to_breaker`
  - `failed_fakeout_flip`
- compare execution models
- evaluate outcomes
- separate local transitions from macro regime

Core files:

- `research_fakeout_reclaim.py`
- future specialized research runners

Outputs:

- reports in `.runtime/`
- family leaderboards
- transition statistics
- candidate execution templates

### Layer F: Execution Research

Purpose:

- turn promising transitions into deterministic trade models

Responsibilities:

- entry models
- stop models
- target models
- fee / slippage-aware evaluation

This layer must remain downstream from transition research.

### Layer G: Consumers

Consumers of Edgerunner truth:

- native frontend inspector
- AI Command Center
- Team Room
- Live Analysis
- later paper/live execution services

Rule:

- consumers must reuse Edgerunner truth
- they must not fork their own interpretation rules

## 3. Timeframe Strategy

Current research stack:

- `1m`
- `3m`
- `5m`
- `10m`
- `15m`
- `30m`
- `1h`
- `2h`
- `4h`
- `1d`
- `1w`

High-value logic:

- `1m` is the entry layer
- `3m`, `5m`, `10m`, `15m` expose local cycle structure
- `30m`, `1h`, `2h`, `4h` carry major intraday / swing context
- `1d`, `1w` define large zones and macro constraints

Monthly exists in parts of the broader system but should be handled carefully
until the data-path naming / table conventions are fully cleaned up.

## 4. Research Philosophy

Edgerunner should optimize for:

- state transitions
- market context
- current regime adaptation

It should avoid:

- single-indicator dogma
- frozen weights from old market conditions
- overfitting to one beautiful window

The intended loop:

1. observe
2. describe
3. contextualize
4. classify transition
5. test execution
6. validate out-of-sample
7. paper trade
8. recalibrate

## 5. Current Known Strengths

Already strong:

- rich per-candle feature store
- native candle inspector
- Seeker Cycle Engine v1
- transition-family research runner
- operational CUDA torch overlay for future numeric acceleration

## 6. Current Weak Points

Still weak or incomplete:

- retest / reclaim persistence at the cycle-object level
- richer MTF context snapshots in native UI
- broader history research performance
- consistent weekly / monthly normalization rules
- unified team-room consumption of native Edgerunner truth

## 7. Performance Plan

Performance work should happen in this order:

### Stage 1

- remove repeated SQLite queries
- cache cycle metrics
- preload repeated TF context

### Stage 2

- vectorize repeated scoring operations
- reduce per-candidate Python branching where possible

### Stage 3

- move suitable numeric scoring / ranking work to CUDA
- only after data access is no longer the bottleneck

Current fact:

- the first big speedup already came from removing repeated SQL calls, not from GPU

## 8. Agent Ownership Map

### Quant Lead

Owns:

- transition family design
- execution family comparison
- profitability claims

### Candle Feature Engineer

Owns:

- raw candle features
- feature correctness

### Seeker Cycle Engineer

Owns:

- cycles
- events
- zone geometry and status

### Data Infrastructure Engineer

Owns:

- timeframe support
- history loading
- migrations
- performance plumbing

### Inspector / UI Engineer

Owns:

- human-readable visibility of candle and cycle truth

### Research Operations Engineer

Owns:

- profiling
- reproducible runs
- CUDA / CPU execution environments

### Validation / Risk Agent

Owns:

- rejecting weak edges
- regime split checks
- walk-forward discipline

## 9. What "Done" Looks Like

A mature Edgerunner should be able to:

- inspect any candle completely
- explain which seeker cycles were active at that time
- show nearest open / killed zones above and below
- classify the current phase
- estimate likely next transitions
- test several execution models
- report which transitions remain profitable in current market conditions

That is the architecture target.
