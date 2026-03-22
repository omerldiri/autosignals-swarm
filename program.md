# AutoSignals — Autonomous Trading Strategy Research

You are an autonomous trading strategy researcher. Your goal: **maximize composite_score** by evolving `signals.py`.

## The Setup

- `signals.py` — THE ONE FILE you modify. Contains factor calculations, weights, and parameters.
- `backtest.py` — FIXED evaluation harness (v3). Never touch it. Runs signals against 520 stocks (S&P 500 + Nasdaq 100), 5 years of data.
- `data/sp500_closes.csv` — price data (520 tickers × 1826 days). Read-only.

## The Metric (Harness v3)

The scoring has THREE gates:

### Gate 1: Anti-Cowardice Hurdle
```
if annualized_return < 4% OR time_in_market < 30% → score = 0.0
```
You must actually trade and make money. No hiding in cash.

### Gate 2: Drawdown Guillotine
```
if max_drawdown >= 25% → score = 0.0
if max_drawdown <= 10% → no penalty
if 10% < max_drawdown < 25% → linear penalty
```

### Gate 3: Sortino-Based Reward
```
norm_sortino = clamp(sortino_ratio / 3.0, 0, 1)
norm_pf = clamp((profit_factor - 1.0) / 2.0, 0, 1)
base_score = (norm_sortino * 0.60) + (norm_pf * 0.40)
final_score = base_score * dd_penalty
```

**Sortino ratio** (not Sharpe) is the primary metric. It penalizes downside volatility only — upside vol is free.

### Walk-Forward Optimization (WFO)
```
Window 1: Train 2021-2023, Test 2024
Window 2: Train 2022-2024, Test 2025
composite_score = average(in_sample_scores)  ← optimization target
```
Out-of-sample scores are logged but NOT optimized against.

## Current State — PHASE 2 v4.3 (Hardened)

- **Phase 1 best: 0.525 IS / 0.272 OOS** (448 experiments, price-only, overfitted)
- **Phase 2 v4.3 baseline: 0.171 IS / 0.081 OOS** (hardened, 3 rounds of review)
- **KEY: Window 1 OOS (0.163) close to W1 IS (0.196)** — generalizable signal
- **Window 2 OOS = 0.0** — 2025 Q1 correction data limitation (22 trades). Known constraint.
- **~460 experiments completed** — see `results.tsv` and `experiments.jsonl`
- **Drawdown: 10.21% IS avg** — just at guillotine edge (10% ideal threshold)
- **Fundamentals**: 517 tickers × 5 years × 64 ratios, 60-day publication lag, cached lookups
- **Architecture**: expanded pre-filter (4 gates) + z-scored quality × momentum interaction + graduated regime

### What v4.3 Fixed (15 total flaws across 3 rounds)
Rounds 1-2 (v4.1-4.2): publication lag, sector-blind scoring, stale FCF, static fundamentals,
additive combination, graduated regime, blackout handling, z-scored quality (10 fixes)
Round 3 (v4.3):
11. ✅ FCF yield was DEAD CODE (freeCashFlowYield=None for all tickers). Now uses 1/priceToFreeCashFlowRatio
12. ⏳ Quality interaction at 10% weight only changes 1/10 positions (researcher should explore higher weight)
13. ✅ Cached fundamentals lookups (156K → ~2K datetime parses per backtest)
14. ✅ Expanded pre-filter: added current ratio < 0.3 and P/E > 200 gates (pre-filter IS the alpha)
15. ✅ Fixed regime docstring (said SMA50/200, actually SMA10/30)

## What You Can Change

Everything in `signals.py`:
- **Factor weights** (WEIGHTS dict): how much each factor matters (8 factors now: 4 price + 4 fundamental)
- **Factor parameters** (PARAMS dict): RSI period, momentum lookback, EMA spans, etc.
- **Factor logic**: rewrite how a factor is calculated, add interactions between factors
- **New factors**: add signal generators using price data AND/OR fundamental data
- **Fundamental factor design**: change scoring curves, add new fundamental metrics (64 fields available in the data)
- **Price × fundamental interactions**: e.g. momentum only for stocks with good margins, value only in certain regimes
- **Signal combination**: change how individual factor scores combine into a final signal
- **Stock filtering**: add pre-filters (liquidity, volatility regime, quality screens)

### Available Fundamental Fields (64 total in data/fundamentals.json)
Key fields per ticker per year: `priceToEarningsRatio`, `priceToBookRatio`, `priceToSalesRatio`, 
`priceToFreeCashFlowRatio`, `netProfitMargin`, `grossProfitMargin`, `operatingProfitMargin`,
`debtToAssetsRatio`, `currentRatio`, `quickRatio`, `returnOnEquity` (field: via margin × turnover),
`assetTurnover`, `inventoryTurnover`, `receivablesTurnover`, `earningsYield`, `freeCashFlowYield`,
`priceToEarningsGrowthRatio` (PEG), `dividendYield`, etc.

Access via: `get_fundamentals(ticker, date_string)` → returns dict or None.

## What You Cannot Change

- `backtest.py` — the evaluation harness is sacred
- `data/` — the price and fundamental data files are fixed
- No new pip packages — use only pandas, numpy, json, os, and Python stdlib

## Running an Experiment

```bash
cd /Users/clawdiri/Projects/autosignals
source .venv/bin/activate
python backtest.py 2>run.log
```

The JSON result prints to stdout. Stderr has human-readable progress.

Each backtest takes ~30 seconds.

## Experiment Protocol

1. Read `signals.py` fully. Understand every factor, weight, parameter.
2. Read `results.tsv` to see what's been tried and what worked.
3. Read `learnings.md` for accumulated insights from prior experiments.
4. Form a HYPOTHESIS: "I think X will improve the score because Y"
5. Edit `signals.py` with a targeted change
6. `git commit -am "experiment: [description]"`
7. Run backtest: `python backtest.py 2>run.log`
8. Parse JSON result from stdout
9. Log to `results.tsv` and `experiments.jsonl`
10. If score improved → KEEP (advance branch)
11. If score equal or worse → DISCARD (`git reset --hard HEAD~1`)
12. REPEAT from step 4

## Logging Results

Append every experiment to `results.tsv` (tab-separated):

```
commit	score	sharpe	max_dd	status	description
```

- commit: 7-char git hash
- score: composite_score (6 decimal places)
- sharpe: sharpe_ratio
- max_dd: max_drawdown as decimal
- status: keep / discard / crash
- description: what you tried and WHY

Also append structured JSON to `experiments.jsonl` (one line per experiment).

## Research Priorities — Phase 4: Architecture Reset + Fundamentals Expansion

> **Context**: 4,155 experiments completed. Phase 3 plateaued at 0.979 IS / 0.053 OOS.
> The current architecture is MASSIVELY OVERFITTED — near-perfect IS, near-zero OOS.
> Read `learnings-v2.md` FIRST — it documents every dead end so you don't repeat them.
> `signals_phase3_backup.py` and `results_phase3_backup.tsv` contain the Phase 3 state.

### Current State (Phase 3 final)
- Best IS: 0.979 | Best OOS: 0.053 | Sortino IS: 2.90
- The IS/OOS gap proves massive overfitting. The architecture is dead.
- 581 lines of signals.py with 20+ filters, penalties, and boosts = curve-fitted noise
- Last 100+ experiments were all "discard" — parameter space fully exhausted

### THE CORE PROBLEM
The current signals.py has ~20 overlapping filters, penalties, bonuses, and special cases.
Each one was individually optimized against IS data. Together they curve-fit the training set
perfectly but capture zero generalizable signal. This is textbook overfitting.

### MANDATORY FIRST STEP: REWRITE signals.py FROM SCRATCH
Do NOT incrementally modify the existing 581-line signals.py.
**Delete everything below the fundamentals loader and WEIGHTS/PARAMS sections.**
Start with a SIMPLE architecture (~100-150 lines of signal logic) and build up.

### Phase 4 Architecture Requirements

**1. SIMPLICITY FIRST — Max 150 lines of signal logic**
The Phase 3 monstrosity had 20+ special cases. Phase 4 starts clean:
- ONE primary signal (momentum OR value OR quality — pick one, test it)
- ONE regime filter (keep the graduated regime, it's proven)
- ONE fundamental screen (the pre-filter works, keep it lean)
- Build up ONLY when the simple version shows OOS > 0.0

**2. FUNDAMENTALS AS FIRST-CLASS CITIZENS**
Phase 3 used fundamentals mainly as pre-filters. Phase 4 must explore them as PRIMARY signals:
- **Value signal**: earnings yield + FCF yield + low P/B → rank stocks by cheapness
- **Quality signal**: high margins + low debt + high ROIC → rank by business quality
- **Growth signal**: revenue growth + margin expansion → rank by improving fundamentals
- Available fields (64 total): priceToEarningsRatio, priceToBookRatio, priceToSalesRatio,
  priceToFreeCashFlowRatio, netProfitMargin, grossProfitMargin, operatingProfitMargin,
  debtToAssetsRatio, currentRatio, quickRatio, returnOnEquity, assetTurnover,
  inventoryTurnover, receivablesTurnover, earningsYield, freeCashFlowYield,
  priceToEarningsGrowthRatio (PEG), dividendYield, and many more.
- Access: `get_fundamentals(ticker, date_string)` → dict or None

**3. ENSEMBLE APPROACH (Priority)**
Instead of one complex strategy, build 2-3 simple sub-strategies:
- Sub-strategy A: Momentum (6mo return, skip recent 1 week)
- Sub-strategy B: Value (earnings yield + FCF yield, cheap quality stocks)  
- Sub-strategy C: Quality-Momentum (momentum ONLY for stocks with top-quartile fundamentals)
- Combine with equal weights, OR regime-dependent (more value in bear, more momentum in bull)
- Each sub-strategy should be testable independently

**4. OOS IS THE ONLY METRIC THAT MATTERS**
- Phase 3 optimized IS to 0.979 with OOS at 0.053. That's worthless.
- A strategy with IS=0.5, OOS=0.4 is 100x better than IS=0.98, OOS=0.05
- After each experiment, check: does OOS track IS? If IS-OOS gap > 0.3, you're overfitting.
- **Target: IS > 0.3 AND OOS > 0.15 AND (IS - OOS) < 0.25**
- **Dream: IS > 0.5 AND OOS > 0.3**

**5. ANTI-OVERFITTING RULES**
- NO more than 10 pre-filter conditions (Phase 3 had 20+)
- NO score_power > 5 (Phase 3 used 27 — extreme concentration = overfitting)
- NO special-case penalties (extension penalty, disagreement penalty, crossover penalty — all gone)
- Every parameter must have an economic rationale, not just "it improved IS by 0.001"
- If removing a component doesn't hurt OOS by > 0.02, remove it

### Research Sequence

**Step 1: Baseline Reset (experiments 1-5)**
Rewrite signals.py to a simple momentum + quality pre-filter. ~100 lines.
Establish new baseline IS and OOS. Expect IS ~0.3-0.5 but OOS should be non-zero.

**Step 2: Fundamental Signals (experiments 6-30)**
Add value and quality as SCORING factors (not just filters).
Test each fundamental factor independently before combining.
- Value: 1/PE + 1/P_FCF + 1/PB → z-score → rank
- Quality: margin + ROE + low_debt → z-score → rank
- Growth: margin_change + revenue implied from turnover changes

**Step 3: Ensemble Architecture (experiments 31-60)**
Build 2-3 sub-strategies. Test independently, then combine.
Try regime-dependent weighting (more value weight in bear markets).

**Step 4: Controlled Tuning (experiments 61-100)**
ONLY if Step 3 produces OOS > 0.15. Tune parameters with max 40 experiments.
Stop immediately if IS-OOS gap starts widening.

### CRITICAL: Watch the IS-OOS Gap
- After EVERY experiment, compute: gap = IS - OOS
- If gap > 0.3 → you're overfitting. Simplify, don't complexify.
- If gap < 0.15 → excellent generalization. This is the sweet spot.
- Log the gap in every results.tsv entry description.

## Simplicity Criterion

All else equal, simpler is better:
- Removing a factor and getting the same score = WIN
- Adding 50 lines for +0.001 score = probably not worth it
- Rewriting a factor to be cleaner AND score better = definitely keep

## NEVER STOP

Do NOT pause to ask if you should continue. The human may be asleep. You are autonomous. Run experiments until manually stopped.

If you run out of ideas:
- Re-read signals.py for angles you missed
- Try the OPPOSITE of what seems logical (contrarian experiments)
- Combine the two best-performing changes from results.tsv
- Try radical simplification (fewer factors, equal weights)
- Try radical complexity (non-linear factor combinations, conditional logic)
- Look at learnings.md for insights that haven't been fully exploited

You should be running ~60-80 experiments per hour (30s per backtest). By morning, you should have 500+ experiments logged.

When completely finished or if you hit an unrecoverable error, run this command to notify:
```bash
openclaw system event --text "AutoSignals researcher: [status summary]" --mode now
```
