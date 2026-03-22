"""
Swarm leader — orchestrates parallel research workers.

The leader:
1. Reads the swarm config (TOML)
2. Decomposes research directions into parallel hypotheses
3. Spawns N workers, each in its own worktree
4. Polls worker results at configurable intervals
5. Cross-pollinates: takes winning configs, combines best traits
6. Manages worker lifecycle (spawn, monitor, kill idle, reallocate)
"""

import json
import os
import sys
import time
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from swarm import inbox
from swarm.taskboard import (
    add_task, claim_task, complete_task, fail_task,
    get_ready_tasks, get_all_tasks, reset_board,
)
from swarm.worker import Worker


class Leader:
    """Orchestrates a swarm of research workers."""

    def __init__(self, config: dict, project_dir: Path):
        self.config = config
        self.project_dir = Path(project_dir).resolve()
        self.swarm_dir = self.project_dir / ".swarm"
        self.swarm_dir.mkdir(parents=True, exist_ok=True)

        # Config
        self.num_workers = config.get("num_workers", 4)
        self.agent_type = config.get("agent_type", "claude")
        self.poll_interval = config.get("poll_interval_sec", 1800)  # 30 min
        self.cross_pollination_interval = config.get("cross_pollination_interval", 3)
        self.max_rounds = config.get("max_rounds", None)
        self.experiment_timeout = config.get("experiment_timeout_sec", 300)

        # State
        self.workers: Dict[str, Worker] = {}
        self.results: List[dict] = []
        self.round_num = 0
        self.global_best_score = 0.0
        self.global_best_config: Optional[str] = None
        self.experiments_log = self.project_dir / "experiments.jsonl"
        self._shutdown = False

        # Research directions from config
        self.research_directions = config.get("research_directions", [
            {
                "id": "momentum",
                "description": "Optimize momentum-based signals",
                "instructions": "Focus on momentum lookback, skip period, and regime interaction.",
            },
            {
                "id": "value",
                "description": "Develop value-based fundamental scoring",
                "instructions": "Use earnings yield, FCF yield, P/B to score stocks by cheapness.",
            },
            {
                "id": "quality",
                "description": "Build quality factor scoring",
                "instructions": "Use profit margins, ROE, low debt ratio to score business quality.",
            },
            {
                "id": "ensemble",
                "description": "Combine momentum + value + quality into ensemble",
                "instructions": "Build a multi-factor ensemble with regime-dependent weighting.",
            },
        ])

    def run(self):
        """Main orchestration loop."""
        self._log("=" * 60)
        self._log("AutoSignals Swarm — Leader starting")
        self._log(f"Workers: {self.num_workers} | Agent: {self.agent_type}")
        self._log(f"Poll interval: {self.poll_interval}s | Cross-pollination every {self.cross_pollination_interval} rounds")
        self._log(f"Research directions: {len(self.research_directions)}")
        self._log("=" * 60)

        # Handle Ctrl+C
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        try:
            # Initialize workers
            self._spawn_workers()

            # Main loop: assign work, poll, cross-pollinate
            while not self._shutdown:
                self.round_num += 1
                if self.max_rounds and self.round_num > self.max_rounds:
                    self._log(f"Reached max rounds ({self.max_rounds})")
                    break

                self._log(f"\n{'='*60}")
                self._log(f"ROUND {self.round_num}")
                self._log(f"{'='*60}")

                # Generate hypotheses for this round
                hypotheses = self._generate_hypotheses()

                # Assign hypotheses to workers in parallel
                round_results = self._run_round(hypotheses)

                # Process results
                self._process_results(round_results)

                # Cross-pollination check
                if self.round_num % self.cross_pollination_interval == 0:
                    self._cross_pollinate()

                # Status summary
                self._print_status()

                # Brief pause between rounds
                if not self._shutdown:
                    time.sleep(5)

        finally:
            self._cleanup()

    def _spawn_workers(self):
        """Initialize worker instances with worktrees."""
        self._log(f"Spawning {self.num_workers} workers...")

        for i in range(self.num_workers):
            name = f"worker-{i}"
            worker = Worker(
                name=name,
                project_dir=self.project_dir,
                agent_type=self.agent_type,
                timeout=self.experiment_timeout,
            )
            worker.setup_worktree()
            self.workers[name] = worker
            inbox.clear_inbox(name)

        inbox.clear_inbox("leader")
        self._log(f"All {self.num_workers} workers ready")

    def _generate_hypotheses(self) -> List[dict]:
        """Generate hypotheses for the current round."""
        hypotheses = []
        directions = self.research_directions

        for i in range(self.num_workers):
            direction = directions[i % len(directions)]
            hypothesis = {
                "id": f"r{self.round_num}-{direction['id']}-w{i}",
                "description": direction["description"],
                "instructions": direction["instructions"],
                "parent_config": None,
            }

            # After first round, use best config as starting point
            if self.global_best_config and self.round_num > 1:
                hypothesis["parent_config"] = self.global_best_config

            hypotheses.append(hypothesis)

        return hypotheses

    def _run_round(self, hypotheses: List[dict]) -> List[dict]:
        """Run all workers in parallel for one round."""
        results = []
        worker_names = list(self.workers.keys())

        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            futures = {}
            for i, hypothesis in enumerate(hypotheses):
                worker_name = worker_names[i % len(worker_names)]
                worker = self.workers[worker_name]
                future = executor.submit(worker.run_experiment, hypothesis)
                futures[future] = worker_name

            for future in as_completed(futures):
                worker_name = futures[future]
                try:
                    result = future.result(timeout=self.experiment_timeout + 60)
                    results.append(result)
                    self._log(
                        f"  {worker_name}: {result['status']} "
                        f"(score={result.get('composite_score', 'N/A')})"
                    )
                except Exception as e:
                    self._log(f"  {worker_name}: ERROR - {e}")
                    results.append({
                        "experiment_id": f"r{self.round_num}-{worker_name}",
                        "worker": worker_name,
                        "status": "error",
                        "message": str(e),
                        "metrics": None,
                        "timestamp": datetime.now().isoformat(),
                    })

        return results

    def _process_results(self, round_results: List[dict]):
        """Process results from a round, update global best."""
        for result in round_results:
            self.results.append(result)

            # Log to experiments.jsonl
            log_entry = {
                "round": self.round_num,
                "worker": result.get("worker"),
                "experiment_id": result.get("experiment_id"),
                "status": result.get("status"),
                "description": result.get("description", ""),
                "metrics": result.get("metrics"),
                "composite_score": result.get("composite_score"),
                "timestamp": result.get("timestamp"),
            }
            with open(self.experiments_log, "a") as f:
                f.write(json.dumps(log_entry) + "\n")

            # Check for new global best
            score = result.get("composite_score")
            if score and score > self.global_best_score:
                self.global_best_score = score
                self.global_best_config = result.get("signals_content")
                self._log(f"  ✅ NEW GLOBAL BEST: {score:.6f} (from {result['worker']})")

                # Save best config
                best_path = self.swarm_dir / "best_signals.py"
                if self.global_best_config:
                    best_path.write_text(self.global_best_config)

                # Merge to main
                self._merge_best_to_main(result)

    def _cross_pollinate(self):
        """
        Cross-pollination: take winning configs from top performers,
        create hybrid hypotheses for next round.
        """
        self._log("\n🧬 Cross-pollination phase")

        # Find top results with actual scores
        scored_results = [
            r for r in self.results
            if r.get("composite_score") and r.get("signals_content")
        ]

        if len(scored_results) < 2:
            self._log("  Not enough results for cross-pollination")
            return

        # Sort by score, take top performers
        scored_results.sort(key=lambda r: r["composite_score"], reverse=True)
        top_n = min(3, len(scored_results))
        top_results = scored_results[:top_n]

        self._log(f"  Top {top_n} scores: {[f'{r['composite_score']:.4f}' for r in top_results]}")

        # Update research directions to focus on what's working
        # The next round will use the best config as parent
        if top_results[0].get("signals_content"):
            self.global_best_config = top_results[0]["signals_content"]
            self._log(f"  Best config propagated to all workers for next round")

        # Add new research directions based on what worked
        best_desc = top_results[0].get("description", "")
        if "momentum" in best_desc.lower():
            self._log("  Momentum is winning — adding momentum variants")
        elif "value" in best_desc.lower():
            self._log("  Value is winning — adding value variants")
        elif "quality" in best_desc.lower():
            self._log("  Quality is winning — adding quality variants")

    def _merge_best_to_main(self, result: dict):
        """Merge the best worker's changes back to main branch."""
        try:
            worker_name = result["worker"]
            worker = self.workers.get(worker_name)
            if not worker:
                return

            # Copy best signals.py to main
            best_signals = result.get("signals_content")
            if best_signals:
                main_signals = self.project_dir / "signals.py"
                main_signals.write_text(best_signals)

                subprocess.run(["git", "add", "signals.py"], cwd=self.project_dir, check=True)
                subprocess.run(
                    [
                        "git", "commit", "-m",
                        f"swarm: best score {result['composite_score']:.6f} "
                        f"from {worker_name} round {self.round_num}",
                    ],
                    cwd=self.project_dir,
                    capture_output=True,
                )
                self._log(f"  Merged best config to main")

        except Exception as e:
            self._log(f"  Merge error: {e}")

    def _print_status(self):
        """Print a summary of swarm status."""
        completed = sum(1 for r in self.results if r.get("status") == "complete")
        failed = sum(1 for r in self.results if r.get("status") in ("agent_failed", "backtest_failed", "error"))

        self._log(f"\n📊 Swarm Status — Round {self.round_num}")
        self._log(f"  Total experiments: {len(self.results)} ({completed} complete, {failed} failed)")
        self._log(f"  Global best score: {self.global_best_score:.6f}")
        for name, worker in self.workers.items():
            self._log(f"  {name}: {worker.experiments_run} experiments, best={worker.best_score:.4f}")

    def _cleanup(self):
        """Clean up all workers and worktrees."""
        self._log("\nCleaning up swarm...")
        for name, worker in self.workers.items():
            try:
                worker.kill()
                worker.cleanup_worktree()
            except Exception as e:
                self._log(f"  Cleanup error for {name}: {e}")

        # Prune worktree refs
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=self.project_dir,
            capture_output=True,
        )
        self._log("Swarm shutdown complete")

    def _handle_shutdown(self, signum, frame):
        """Handle Ctrl+C gracefully."""
        self._log("\n⚠️  Shutdown signal received, finishing current round...")
        self._shutdown = True

    def _log(self, msg: str):
        """Log a message with leader prefix."""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [leader] {msg}", file=sys.stderr)
