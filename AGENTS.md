# AGENTS.md - Edgerunner

This file defines how work in `edgerunner_restored` should be organized.

The goal of Edgerunner is not to collect random indicator signals. The goal is to
build a market-state engine that:

- describes every candle completely
- reconstructs liquidity / seeker cycles
- studies state transitions such as fakeout -> reclaim -> run
- turns robust transitions into paper-tradeable and later live-tradeable logic

## Project Truth

Edgerunner has four layers:

1. `Raw Market Data`
   - OHLCV
   - spot / futures volume
   - delta
   - whale / liquidation overlays where available

2. `Candle Feature Layer`
   - one row per candle per timeframe
   - all feature truth belongs here first
   - no hand-wavy interpretation should replace missing candle fields

3. `Cycle / Context Layer`
   - seeker cycles
   - zone placement
   - kill / div / retest / reclaim timing
   - nearest open / killed HS / LS zones
   - MTF context joins

4. `Research / Execution Layer`
   - transition families
   - entry / stop / TP research
   - validation
   - paper trading

Rule: if a concept belongs to a single candle, it must exist in the candle layer
before anyone builds interpretation on top of it.

## Current Source of Truth

Native Edgerunner is the primary source of truth.

- `candle_analyzer.py`
  - per-candle feature generation
- `db.py`
  - candle persistence and timeframe schema
- `seeker_cycles.py`
  - cycle reconstruction and persistence
- `edgerunner_server.py`
  - API surface for UI and downstream consumers
- `research_fakeout_reclaim.py`
  - quant research runner
- `frontend/src/components/dashboard/FeatureTab.jsx`
  - native candle inspector

`ai-command-center` may consume Edgerunner, but it must not invent a second,
conflicting interpretation model.

## Agent Responsibilities

Use these roles to keep the codebase understandable as it grows.

### 1. Quant Lead

Owns:

- research hypotheses
- transition-family definitions
- backtests
- execution models
- walk-forward and paper-readiness decisions

Questions this agent answers:

- What transition families actually have edge?
- Which entry / stop / TP model is robust?
- Which setups are noise, fakeout, reclaim, breaker, continuation?

Main files:

- `research_fakeout_reclaim.py`
- `scenarios.py`
- future research runners / notebooks / reports

### 2. Candle Feature Engineer

Owns:

- all raw per-candle features
- feature correctness
- feature naming
- feature completeness

Questions this agent answers:

- Does each candle expose the full truth we need?
- Are `bos`, `choch`, `div`, `seeker`, volume, break-depth, shape fields correct?
- Is a missing context feature actually a missing candle field?

Main files:

- `candle_analyzer.py`
- `db.py`

Rule:

- never hide a missing candle truth behind a downstream heuristic

### 3. Seeker Cycle Engineer

Owns:

- cycle reconstruction
- cycle status
- cycle event history
- zone geometry
- nearest open / killed zone queries

Questions this agent answers:

- Which cycle is active?
- Was the market inside an open zone, killed zone, or outside both?
- How old is the cycle?
- How many divs has it produced?
- Was the kill fresh or stale?

Main files:

- `seeker_cycles.py`
- `edgerunner_server.py`
- cycle-related SQL in `db.py` if needed

### 4. Data Infrastructure Engineer

Owns:

- timeframe coverage
- history backfill
- source adapters
- DB migration safety
- performance of data access paths

Questions this agent answers:

- Do we have enough history for each timeframe?
- Are `3m`, `10m`, `2h`, `1w`, `1M` consistent?
- Are performance problems due to SQL, Python loops, or source access?

Main files:

- `db.py`
- `history_loader.py`
- `hyperliquid_api.py`
- migration / backfill helpers

### 5. Inspector / UI Engineer

Owns:

- native candle inspector
- grouped feature visibility
- neighbor candle context
- cycle context visibility

Questions this agent answers:

- Can a human read one candle completely?
- Can a human see the nearby event context without leaving the inspector?
- Are badges reflecting current truth or stale heuristics?

Main files:

- `frontend/src/components/dashboard/FeatureTab.jsx`
- `frontend/src/components/dashboard/FeatureTab.css`
- `frontend/src/lib/featureGroups.js`

### 6. Research Operations Engineer

Owns:

- long runs
- profiling
- acceleration paths
- CUDA / CPU execution wrappers
- report reproducibility

Questions this agent answers:

- Is the bottleneck SQL, Python, or numeric compute?
- Should this path stay on CPU or move to CUDA?
- Are research runs reproducible and documented?

Main files:

- `research_fakeout_reclaim.py`
- `run_with_cuda_torch.sh`
- runtime reports in `.runtime/`

### 7. Validation and Risk Agent

Owns:

- evidence thresholds
- sample quality
- regime split checks
- anti-overfitting discipline

Questions this agent answers:

- Is this edge real or just a nice slice?
- Does it survive fees, slippage, and walk-forward?
- Is the sample too thin?

This role may work through the same research runner, but its job is to say "no"
when the result is fragile.

## Non-Negotiable Engineering Rules

1. Do not compress away candle detail for speed.
   - optimize execution
   - do not throw away market truth

2. Do not let UI heuristics become source of truth.
   - UI reads backend truth
   - it does not invent it

3. Do not mix candle truth and interpretation.
   - candle feature first
   - cycle/context second
   - transition interpretation third

4. Do not hard-code old beliefs as permanent rules.
   - weights and rules must be revisable by outcome research

5. Do not trust single-window wins.
   - all promising families need broader slices and walk-forward confirmation

## Current Strategic Focus

These are the current top priorities for Edgerunner:

1. Keep the native candle inspector complete and correct.
2. Strengthen seeker cycle context and MTF zone mapping.
3. Research state transitions, not isolated candle signals.
4. Separate:
   - regime direction
   - micro fakeout direction
   - reclaim / breaker continuation
5. Use current outcomes to recalibrate logic instead of freezing stale weights.

## Transition Families We Care About

Current research focus:

- `fakeout_to_reclaim`
- `fakeout_to_breaker`
- `failed_fakeout_flip`

These are not opinions. They are research families to test, compare, and kill if
they stop working.

## Performance Policy

The repo has enough machine power available, including a local CUDA torch overlay.
But performance work must follow this order:

1. remove redundant SQL and Python orchestration
2. cache repeated context
3. vectorize in memory
4. only then move suitable batched math to CUDA

Rule:

- GPU is for numeric throughput
- GPU is not a cure for badly shaped data access

## Working Agreement For Future Agents

If you touch this repo:

- say which layer you are changing
- keep responsibilities separated
- document architectural changes
- prefer improving source truth over adding downstream guesswork

If you create a new feature:

- decide whether it belongs to:
  - candle layer
  - cycle layer
  - research layer
  - UI layer
- put it there on purpose

If you are unsure:

- candle truth first
- interpretation later
