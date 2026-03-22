#!/usr/bin/env python3
"""
Quick integration test for AutoSignals Swarm.

Tests:
1. Worker worktree creation
2. Inbox messaging
3. Task board dependency resolution
4. Config loading
"""

import os
import sys
import json
import shutil
from pathlib import Path

# Add swarm to path
sys.path.insert(0, str(Path(__file__).parent))

from swarm import inbox, taskboard
from swarm.worker import Worker
from swarm.leader import Leader


def test_inbox():
    """Test filesystem-based inbox."""
    print("Testing inbox...")
    
    # Clear test inboxes
    inbox.clear_inbox("test_sender")
    inbox.clear_inbox("test_receiver")
    
    # Send message
    msg_id = inbox.send(
        sender="test_sender",
        recipient="test_receiver",
        msg_type="test",
        content={"foo": "bar"},
    )
    assert msg_id, "Message ID should be returned"
    
    # Peek without consuming
    messages = inbox.peek("test_receiver", msg_type="test")
    assert len(messages) == 1, f"Should have 1 message, got {len(messages)}"
    assert messages[0]["content"]["foo"] == "bar"
    
    # Receive and consume
    received = inbox.receive("test_receiver")
    assert len(received) == 1
    
    # Should be empty now
    assert inbox.count("test_receiver") == 0
    
    print("  ✅ Inbox tests passed")


def test_taskboard():
    """Test task board with dependency resolution."""
    print("Testing taskboard...")
    
    # Reset board
    taskboard.reset_board()
    
    # Add tasks with dependencies
    t1 = taskboard.add_task("task-1", "First task")
    t2 = taskboard.add_task("task-2", "Second task", blocked_by=["task-1"])
    t3 = taskboard.add_task("task-3", "Third task", blocked_by=["task-1", "task-2"])
    
    # t1 should be ready, t2 and t3 should be blocked
    assert t1["status"] == "ready"
    assert t2["status"] == "blocked"
    assert t3["status"] == "blocked"
    
    # Complete t1 — t2 should auto-unblock
    taskboard.complete_task("task-1")
    tasks = taskboard.get_all_tasks()
    assert tasks["task-2"]["status"] == "ready"
    assert tasks["task-3"]["status"] == "blocked"  # still waiting on task-2
    
    # Complete t2 — t3 should auto-unblock
    taskboard.complete_task("task-2")
    tasks = taskboard.get_all_tasks()
    assert tasks["task-3"]["status"] == "ready"
    
    print("  ✅ Taskboard tests passed")


def test_worker_worktree():
    """Test worker worktree creation (requires git repo)."""
    print("Testing worker worktree...")
    
    project_dir = Path(__file__).parent.resolve()
    
    # Ensure we're in a git repo
    if not (project_dir / ".git").exists():
        print("  ⚠️  Skipped (not a git repo)")
        return
    
    worker = Worker(
        name="test-worker",
        project_dir=project_dir,
        agent_type="claude",
        timeout=60,
    )
    
    try:
        # Create worktree
        worker.setup_worktree()
        assert worker.worktree_dir.exists(), "Worktree directory should exist"
        assert (worker.worktree_dir / "signals.py").exists(), "signals.py should exist in worktree"
        assert (worker.worktree_dir / "backtest.py").exists(), "backtest.py should exist in worktree"
        
        print("  ✅ Worker worktree tests passed")
    
    finally:
        # Cleanup
        worker.cleanup_worktree()
        assert not worker.worktree_dir.exists(), "Worktree should be cleaned up"


def test_config_loading():
    """Test TOML config loading."""
    print("Testing config loading...")
    
    from run_swarm import load_config, DEFAULT_CONFIG
    
    # Load default config
    config = load_config(config_path=None)
    assert config["num_workers"] == DEFAULT_CONFIG["num_workers"]
    assert "research_directions" in config
    assert len(config["research_directions"]) > 0
    
    # Load from swarm.toml if it exists
    if Path("swarm.toml").exists():
        config = load_config("swarm.toml")
        assert config["num_workers"] >= 1
        assert config["agent_type"] in ["claude", "codex"]
    
    print("  ✅ Config loading tests passed")


def main():
    print("=" * 60)
    print("AutoSignals Swarm — Integration Tests")
    print("=" * 60)
    print()
    
    try:
        test_inbox()
        test_taskboard()
        test_config_loading()
        test_worker_worktree()
        
        print()
        print("=" * 60)
        print("All tests passed! ✅")
        print("=" * 60)
        return 0
    
    except AssertionError as e:
        print()
        print("=" * 60)
        print(f"Test failed: {e}")
        print("=" * 60)
        return 1
    
    except Exception as e:
        print()
        print("=" * 60)
        print(f"Error: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
