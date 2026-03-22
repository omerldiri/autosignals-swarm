#!/usr/bin/env python3
"""
AutoSignals Swarm — Parallel Research with Cross-Pollination

Leader-worker architecture for autonomous trading signal optimization.
Spawns N workers in isolated git worktrees, each running independent
experiments, with periodic cross-pollination of winning configs.

Usage:
    python run_swarm.py                          # Use default config
    python run_swarm.py --config swarm.toml      # Custom config
    python run_swarm.py --workers 8 --rounds 10  # CLI overrides
    python run_swarm.py --dry-run                # Validate config only
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # pip install tomli for <3.11
    except ImportError:
        tomllib = None


DEFAULT_CONFIG = {
    "num_workers": 4,
    "agent_type": "claude",
    "poll_interval_sec": 1800,
    "cross_pollination_interval": 3,
    "max_rounds": None,
    "experiment_timeout_sec": 300,
    "research_directions": [
        {
            "id": "momentum",
            "description": "Optimize momentum-based signals — lookback period, skip window, regime sensitivity",
            "instructions": (
                "Focus on momentum factor: try different lookback periods (60-252 days), "
                "skip recent 1-5 days, adjust regime threshold. Keep it simple."
            ),
        },
        {
            "id": "value",
            "description": "Develop value-based scoring using fundamental data",
            "instructions": (
                "Build a value score: earnings yield + FCF yield + low P/B. "
                "Use get_fundamentals() to access 64 fundamental fields. "
                "Score stocks by cheapness relative to quality."
            ),
        },
        {
            "id": "quality",
            "description": "Build quality factor from profit margins, ROE, and balance sheet strength",
            "instructions": (
                "Create a quality score: high net profit margin + high gross margin + low debt ratio. "
                "Use get_fundamentals(). Quality should filter OUT bad stocks, not just rank."
            ),
        },
        {
            "id": "ensemble",
            "description": "Combine momentum + value + quality into a multi-factor ensemble",
            "instructions": (
                "Build an ensemble: equal-weight or regime-dependent weighting of momentum, "
                "value, and quality factors. More momentum in bull markets, more value in bears. "
                "Target IS-OOS gap < 0.25."
            ),
        },
    ],
}


def load_config(config_path: str = None) -> dict:
    """Load config from TOML file, falling back to defaults."""
    config = dict(DEFAULT_CONFIG)

    if config_path and Path(config_path).exists():
        if tomllib is None:
            print(
                "Warning: tomllib not available (Python <3.11). "
                "Install `tomli` or use Python 3.11+. Using defaults.",
                file=sys.stderr,
            )
            return config

        with open(config_path, "rb") as f:
            toml_config = tomllib.load(f)

        # Merge TOML into defaults
        swarm_section = toml_config.get("swarm", {})
        for key in ("num_workers", "agent_type", "poll_interval_sec",
                     "cross_pollination_interval", "max_rounds", "experiment_timeout_sec"):
            if key in swarm_section:
                config[key] = swarm_section[key]

        if "research_directions" in toml_config:
            directions = []
            for d in toml_config["research_directions"]:
                directions.append({
                    "id": d.get("id", "unnamed"),
                    "description": d.get("description", ""),
                    "instructions": d.get("instructions", ""),
                })
            config["research_directions"] = directions

    return config


def main():
    parser = argparse.ArgumentParser(
        description="AutoSignals Swarm — Parallel research with cross-pollination"
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="swarm.toml",
        help="Path to TOML config file (default: swarm.toml)",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="Number of parallel workers (overrides config)",
    )
    parser.add_argument(
        "--rounds", "-r",
        type=int,
        default=None,
        help="Maximum rounds (overrides config)",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        choices=["claude", "codex"],
        help="Agent type (overrides config)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Experiment timeout in seconds (overrides config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and print plan without running",
    )
    args = parser.parse_args()

    # Load config
    config = load_config(args.config if Path(args.config).exists() else None)

    # CLI overrides
    if args.workers is not None:
        config["num_workers"] = args.workers
    if args.rounds is not None:
        config["max_rounds"] = args.rounds
    if args.agent is not None:
        config["agent_type"] = args.agent
    if args.timeout is not None:
        config["experiment_timeout_sec"] = args.timeout

    # Project dir is where this script lives
    project_dir = Path(__file__).parent.resolve()

    # Validate
    if not (project_dir / "backtest.py").exists():
        print("Error: backtest.py not found in project directory", file=sys.stderr)
        sys.exit(1)
    if not (project_dir / "signals.py").exists():
        print("Error: signals.py not found in project directory", file=sys.stderr)
        sys.exit(1)

    # Dry run
    if args.dry_run:
        print("=" * 60)
        print("AutoSignals Swarm — Dry Run")
        print("=" * 60)
        print(f"\nProject dir: {project_dir}")
        print(f"Workers:     {config['num_workers']}")
        print(f"Agent type:  {config['agent_type']}")
        print(f"Max rounds:  {config.get('max_rounds', 'unlimited')}")
        print(f"Poll interval: {config['poll_interval_sec']}s")
        print(f"Cross-pollination every: {config['cross_pollination_interval']} rounds")
        print(f"Experiment timeout: {config['experiment_timeout_sec']}s")
        print(f"\nResearch directions ({len(config['research_directions'])}):")
        for d in config["research_directions"]:
            print(f"  - {d['id']}: {d['description']}")
        print(f"\nConfig OK ✅")
        return

    # Ensure git is initialized
    if not (project_dir / ".git").exists():
        import subprocess
        subprocess.run(["git", "init"], cwd=project_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=project_dir, check=True)

    # Launch the swarm
    from swarm.leader import Leader
    leader = Leader(config=config, project_dir=project_dir)
    leader.run()


if __name__ == "__main__":
    main()
