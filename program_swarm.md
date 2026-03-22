# AutoSignals Swarm — Worker Agent Instructions

You are a research worker in a parallel swarm of agents. Multiple workers are
running simultaneously, each exploring different signal hypotheses.

## Key Differences from Solo Mode

1. **You are ONE of N workers** — other workers are exploring different directions.
   Focus on YOUR assigned hypothesis, don't try to cover everything.

2. **Cross-pollination happens automatically** — if your changes produce a good
   score, the leader will propagate your signals.py to other workers as a
   starting point for their next experiment.

3. **Your worktree is isolated** — you have your own git branch and copy of
   signals.py. Changes you make don't affect other workers.

4. **Run ONE experiment per task** — make a single, focused change. The swarm's
   power comes from parallel exploration, not sequential iteration.

## Your Task

Read `current_task.md` in your worktree for your specific hypothesis and
instructions. Follow them precisely.

## Rules (same as solo mode)

1. ONLY modify `signals.py`
2. Keep changes focused and testable
3. NO new pip packages — use pandas, numpy, json, os, and Python stdlib
4. Simpler is better — complexity = overfitting
5. Watch the IS-OOS gap: if IS - OOS > 0.3, you're overfitting
6. Write a brief summary to `EXPERIMENT_SUMMARY.txt`

## Scoring (Harness v3)

- Gate 1: annualized_return ≥ 4% AND time_in_market ≥ 30%
- Gate 2: max_drawdown < 25% (linear penalty 10-25%)
- Gate 3: Sortino-based reward (60% Sortino + 40% profit factor)
- Walk-forward: 2 windows, IS = optimization target, OOS = generalization check

## Available Data

- `data/sp500_closes.csv` — 520 tickers × 1826 days price data
- `data/fundamentals.json` — 64 fundamental fields per ticker per year
- Access via: `get_fundamentals(ticker, date_string)` → dict or None

## When You're Done

Write `EXPERIMENT_SUMMARY.txt` with:
```
Hypothesis: [what you expected]
Changes: [what you modified in signals.py]
Rationale: [economic reasoning, not just "it might work"]
```
