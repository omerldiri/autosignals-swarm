"""
Swarm worker — runs experiments in an isolated git worktree.

Each worker:
1. Gets its own worktree (copy of main branch)
2. Receives a hypothesis via inbox
3. Spawns a coding agent to modify signals.py
4. Runs backtest.py
5. Reports results back to leader via inbox
6. Cleans up worktree on exit
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from swarm import inbox


class Worker:
    """An isolated research worker with its own git worktree."""

    def __init__(
        self,
        name: str,
        project_dir: Path,
        agent_type: str = "claude",
        timeout: int = 300,
    ):
        self.name = name
        self.project_dir = Path(project_dir).resolve()
        self.agent_type = agent_type
        self.timeout = timeout
        self.worktree_dir = self.project_dir / ".swarm" / "worktrees" / name
        self.experiments_run = 0
        self.best_score = 0.0
        self.pid: Optional[int] = None
        self._alive = True

    def setup_worktree(self):
        """Create an isolated git worktree for this worker."""
        self.worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # Clean up stale worktree if it exists
        if self.worktree_dir.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.worktree_dir)],
                cwd=self.project_dir,
                capture_output=True,
            )

        # Create a new branch for this worker
        branch_name = f"swarm/{self.name}"
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=self.project_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(self.worktree_dir), "main"],
            cwd=self.project_dir,
            check=True,
        )

        # Symlink data directory to save disk space
        worktree_data = self.worktree_dir / "data"
        source_data = self.project_dir / "data"
        if source_data.exists() and not worktree_data.exists():
            worktree_data.symlink_to(source_data)

        # Copy .env if it exists
        env_src = self.project_dir / ".env"
        if env_src.exists():
            import shutil
            shutil.copy2(env_src, self.worktree_dir / ".env")

        self._log(f"Worktree created at {self.worktree_dir}")

    def cleanup_worktree(self):
        """Remove the worker's git worktree."""
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.worktree_dir)],
                cwd=self.project_dir,
                capture_output=True,
            )
            branch_name = f"swarm/{self.name}"
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=self.project_dir,
                capture_output=True,
            )
            self._log("Worktree cleaned up")
        except Exception as e:
            self._log(f"Cleanup error: {e}")

    def run_experiment(self, hypothesis: dict) -> dict:
        """
        Run a single experiment based on a hypothesis.

        Args:
            hypothesis: {
                "id": str,
                "description": str,
                "instructions": str,  # what to change in signals.py
                "parent_config": str | None,  # signals.py content to start from
            }

        Returns:
            Result dict with metrics, status, etc.
        """
        exp_id = hypothesis.get("id", f"exp-{self.experiments_run}")
        self._log(f"Starting experiment {exp_id}: {hypothesis.get('description', 'N/A')}")

        # If parent config provided (cross-pollination), write it
        if hypothesis.get("parent_config"):
            signals_path = self.worktree_dir / "signals.py"
            signals_path.write_text(hypothesis["parent_config"])
            self._git_commit(f"cross-pollination base for {exp_id}")

        # Create task file for the coding agent
        task_content = self._build_task(hypothesis)
        task_file = self.worktree_dir / "current_task.md"
        task_file.write_text(task_content)

        # Spawn the coding agent
        agent_result = self._spawn_agent(task_file)

        if not agent_result["success"]:
            self._log(f"Agent failed: {agent_result['message']}")
            return {
                "experiment_id": exp_id,
                "worker": self.name,
                "status": "agent_failed",
                "message": agent_result["message"],
                "metrics": None,
                "timestamp": datetime.now().isoformat(),
            }

        # Check if signals.py was actually modified
        diff = subprocess.run(
            ["git", "diff", "--stat", "signals.py"],
            cwd=self.worktree_dir,
            capture_output=True,
            text=True,
        )
        if not diff.stdout.strip():
            return {
                "experiment_id": exp_id,
                "worker": self.name,
                "status": "no_changes",
                "message": "Agent made no changes to signals.py",
                "metrics": None,
                "timestamp": datetime.now().isoformat(),
            }

        # Commit changes
        self._git_commit(f"experiment {exp_id}: {hypothesis.get('description', '')[:80]}")

        # Run backtest
        metrics = self._run_backtest()
        if metrics is None:
            return {
                "experiment_id": exp_id,
                "worker": self.name,
                "status": "backtest_failed",
                "message": "Backtest crashed or timed out",
                "metrics": None,
                "timestamp": datetime.now().isoformat(),
            }

        # Read the current signals.py for potential cross-pollination
        signals_content = (self.worktree_dir / "signals.py").read_text()

        score = metrics.get("composite_score", 0.0)
        self.experiments_run += 1
        if score > self.best_score:
            self.best_score = score

        return {
            "experiment_id": exp_id,
            "worker": self.name,
            "status": "complete",
            "description": hypothesis.get("description", ""),
            "metrics": metrics,
            "composite_score": score,
            "signals_content": signals_content,
            "timestamp": datetime.now().isoformat(),
        }

    def _build_task(self, hypothesis: dict) -> str:
        """Build the task prompt for the coding agent."""
        program = ""
        program_path = self.worktree_dir / "program.md"
        if program_path.exists():
            program = program_path.read_text()

        signals = (self.worktree_dir / "signals.py").read_text()

        return f"""# Research Task for Worker: {self.name}

## Hypothesis
{hypothesis.get('description', 'Improve the composite score.')}

## Specific Instructions
{hypothesis.get('instructions', 'Make a targeted change to improve the score.')}

## Current signals.py
```python
{signals}
```

## Program Context
{program}

## Rules
1. ONLY modify signals.py
2. Make ONE focused change per experiment
3. Keep it simple — complexity = overfitting
4. Write a brief summary of what you changed to EXPERIMENT_SUMMARY.txt
"""

    def _spawn_agent(self, task_file: Path) -> dict:
        """Spawn a coding agent to modify signals.py."""
        try:
            if self.agent_type == "claude":
                result = subprocess.run(
                    [
                        "claude",
                        "--print",
                        "--dangerously-skip-permissions",
                        "-p", task_file.read_text(),
                    ],
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            elif self.agent_type == "codex":
                result = subprocess.run(
                    ["codex", "--prompt-file", str(task_file), "--approval-mode", "full-auto"],
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            else:
                return {"success": False, "message": f"Unknown agent type: {self.agent_type}"}

            # Check for experiment summary
            summary_file = self.worktree_dir / "EXPERIMENT_SUMMARY.txt"
            message = ""
            if summary_file.exists():
                message = summary_file.read_text()
                summary_file.unlink()
            elif result.stdout:
                message = result.stdout[:500]
            else:
                message = "Agent completed"

            return {"success": result.returncode == 0, "message": message}

        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Agent timeout"}
        except FileNotFoundError:
            return {"success": False, "message": f"Agent binary '{self.agent_type}' not found"}
        except Exception as e:
            return {"success": False, "message": f"Agent error: {e}"}

    def _run_backtest(self) -> Optional[dict]:
        """Run backtest.py in the worktree and return metrics."""
        try:
            # Use the project's venv if it exists
            python = str(self.project_dir / ".venv" / "bin" / "python")
            if not os.path.exists(python):
                python = sys.executable

            result = subprocess.run(
                [python, "backtest.py"],
                cwd=self.worktree_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode != 0:
                self._log(f"Backtest failed: {result.stderr[:200]}")
                return None

            return json.loads(result.stdout)

        except subprocess.TimeoutExpired:
            self._log("Backtest timeout")
            return None
        except json.JSONDecodeError as e:
            self._log(f"Failed to parse backtest output: {e}")
            return None
        except Exception as e:
            self._log(f"Backtest error: {e}")
            return None

    def _git_commit(self, message: str):
        """Commit changes in the worktree."""
        subprocess.run(["git", "add", "signals.py"], cwd=self.worktree_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=self.worktree_dir,
            capture_output=True,
        )

    def _log(self, msg: str):
        """Log a message with worker name prefix."""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [{self.name}] {msg}", file=sys.stderr)

    def kill(self):
        """Signal this worker to stop."""
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive
