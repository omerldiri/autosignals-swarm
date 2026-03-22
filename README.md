# AutoSignals Swarm

Parallel trading signal optimization with agent swarm intelligence. An evolution of [Karpathy's autoresearch](https://github.com/karpathy/autoresearch) pattern, adapted for quantitative finance and scaled from sequential to parallel execution using a leader-worker architecture inspired by [ClawTeam](https://github.com/HKUDS/ClawTeam).

## Architecture

```
                    ┌─────────────────────┐
                    │       Leader        │
                    │  - Decompose tasks  │
                    │  - Poll results     │
                    │  - Cross-pollinate  │
                    │  - Merge winners    │
                    └──────┬──────────────┘
                           │
              ┌────────────┼────────────┬────────────┐
              ▼            ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Worker 0 │ │ Worker 1 │ │ Worker 2 │ │ Worker 3 │
        │ momentum │ │  value   │ │ quality  │ │ ensemble │
        │          │ │          │ │          │ │          │
        │ worktree │ │ worktree │ │ worktree │ │ worktree │
        │ ┌──────┐ │ │ ┌──────┐ │ │ ┌──────┐ │ │ ┌──────┐ │
        │ │signal│ │ │ │signal│ │ │ │signal│ │ │ │signal│ │
        │ │ .py  │ │ │ │ .py  │ │ │ │ .py  │ │ │ │ .py  │ │
        │ └──┬───┘ │ │ └──┬───┘ │ │ └──┬───┘ │ │ └──┬───┘ │
        │    ▼     │ │    ▼     │ │    ▼     │ │    ▼     │
        │ backtest │ │ backtest │ │ backtest │ │ backtest │
        │    ▼     │ │    ▼     │ │    ▼     │ │    ▼     │
        │  score   │ │  score   │ │  score   │ │  score   │
        └──────────┘ └──────────┘ └──────────┘ └──────────┘
              │            │            │            │
              └────────────┴────────────┴────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   Cross-Pollination   │
                    │  Top configs merged   │
                    │  → next round seeds   │
                    └───────────────────────┘
```

## Sequential vs. Swarm

| | Sequential (`run.py`) | Swarm (`run_swarm.py`) |
|---|---|---|
| **Parallelism** | 1 experiment at a time | N workers simultaneously |
| **Isolation** | Single git branch | Separate git worktrees |
| **Exploration** | One direction per step | Multiple directions at once |
| **Knowledge sharing** | N/A | Cross-pollination of winners |
| **Speed** | ~60 experiments/hour | ~60×N experiments/hour |
| **Coordination** | None needed | Filesystem-based inbox |

## Quick Start

```bash
# Clone
git clone https://github.com/omerldiri/autosignals-swarm.git
cd autosignals-swarm

# Setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Download data (if not cached)
python prepare.py

# Dry run — validate config
python run_swarm.py --dry-run

# Launch the swarm
python run_swarm.py

# Or with options
python run_swarm.py --workers 8 --rounds 20 --agent claude

# Monitor with tmux dashboard
./swarm-board.sh
```

## Configuration

Edit `swarm.toml` to customize:

```toml
[swarm]
num_workers = 4                  # Parallel workers
agent_type = "claude"            # "claude" or "codex"
poll_interval_sec = 1800         # Leader poll interval
cross_pollination_interval = 3   # Cross-pollinate every N rounds
experiment_timeout_sec = 300     # Per-experiment timeout

[[research_directions]]
id = "momentum"
description = "Optimize momentum signals"
instructions = "Focus on lookback period, skip window..."

[[research_directions]]
id = "value"
description = "Build value scoring from fundamentals"
instructions = "Use earnings yield, FCF yield, P/B..."
```

## How Cross-Pollination Works

1. Workers run experiments in parallel, each exploring a different research direction
2. Every N rounds, the leader collects all results
3. Top-performing `signals.py` configs are ranked by composite score
4. The best config is propagated to ALL workers as the starting point for their next experiment
5. Workers then apply their specific research direction ON TOP of the proven base
6. This creates a genetic-algorithm-like evolution: good traits survive and combine

The filesystem-based inbox (`.swarm/inbox/`) enables coordination without external dependencies — just JSON files on disk, following the ClawTeam pattern.

## Project Structure

```
autosignals-swarm/
├── run_swarm.py          ← Main entry point (leader-worker orchestration)
├── swarm.toml            ← Swarm configuration
├── swarm-board.sh        ← Tmux dashboard
│
├── swarm/                ← Swarm infrastructure
│   ├── leader.py         ← Leader agent (orchestration, cross-pollination)
│   ├── worker.py         ← Worker agent (isolated experiments)
│   ├── inbox.py          ← Filesystem-based messaging
│   └── taskboard.py      ← Task DAG with dependency resolution
│
├── signals.py            ← THE modifiable file (trading signals)
├── backtest.py           ← FIXED evaluation harness (sacred)
├── prepare.py            ← Data download script
├── program.md            ← Agent instructions (solo mode)
├── program_swarm.md      ← Agent instructions (swarm mode)
│
├── data/                 ← Price + fundamental data (cached)
├── experiments.jsonl     ← Full experiment log
├── best_score.json       ← Current best score
│
└── .swarm/               ← Runtime state (gitignored)
    ├── inbox/            ← Inter-agent messages
    ├── worktrees/        ← Isolated git worktrees per worker
    ├── tasks.json        ← Task board with dependency DAG
    └── best_signals.py   ← Best config found so far
```

## Core Files (from AutoSignals)

- **`signals.py`** — The ONE file agents modify. Contains factor calculations, weights, parameters.
- **`backtest.py`** — Fixed evaluation harness. Walk-forward optimization with Sortino-based scoring, drawdown gates, anti-cowardice hurdles. Never modified.
- **`prepare.py`** — Downloads S&P 500 + Nasdaq 100 price data and fundamental data.
- **`program.md`** — Instructions that tell AI agents how to run experiments.

## Credits

- [Andrej Karpathy](https://github.com/karpathy) — [autoresearch](https://github.com/karpathy/autoresearch) pattern (autonomous research loop)
- [HKUDS](https://github.com/HKUDS) — [ClawTeam](https://github.com/HKUDS/ClawTeam) (multi-agent coordination via filesystem inbox, task DAG)

## License

MIT
