#!/usr/bin/env bash
# AutoSignals Swarm Dashboard — tmux-based worker monitoring
#
# Usage:
#   ./swarm-board.sh              # Watch all workers (default 4)
#   ./swarm-board.sh 8            # Watch 8 workers
#   ./swarm-board.sh --kill       # Kill the dashboard session
#
# Layout:
#   ┌────────────────────┬────────────────────┐
#   │     worker-0       │     worker-1       │
#   │  (tail worktree)   │  (tail worktree)   │
#   ├────────────────────┼────────────────────┤
#   │     worker-2       │     worker-3       │
#   │  (tail worktree)   │  (tail worktree)   │
#   ├────────────────────┴────────────────────┤
#   │           leader status                 │
#   └─────────────────────────────────────────┘

set -euo pipefail

SESSION="autosignals-swarm"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
NUM_WORKERS="${1:-4}"

if [[ "${1:-}" == "--kill" ]]; then
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Dashboard killed" || echo "No dashboard running"
    exit 0
fi

# Kill existing session
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Create new session with leader pane
tmux new-session -d -s "$SESSION" -n "swarm" \
    "watch -n 5 'echo \"=== AutoSignals Swarm Status ===\"; echo; cat ${PROJECT_DIR}/.swarm/tasks.json 2>/dev/null | python3 -m json.tool 2>/dev/null || echo \"No tasks yet\"; echo; echo \"=== Recent Experiments ===\"; tail -5 ${PROJECT_DIR}/experiments.jsonl 2>/dev/null | python3 -c \"import sys,json; [print(f\\\"  {json.loads(l).get(\\x27experiment_id\\x27,\\x27?\\x27)}: score={json.loads(l).get(\\x27composite_score\\x27,\\x27N/A\\x27)} [{json.loads(l).get(\\x27status\\x27,\\x27?\\x27)}]\\\") for l in sys.stdin]\" 2>/dev/null || echo \"No experiments yet\"; echo; echo \"=== Global Best ===\"; cat ${PROJECT_DIR}/.swarm/best_signals.py 2>/dev/null | head -5 || echo \"No best config yet\"'"

# Add worker panes
for i in $(seq 0 $((NUM_WORKERS - 1))); do
    WORKTREE="${PROJECT_DIR}/.swarm/worktrees/worker-${i}"
    tmux split-window -t "$SESSION" \
        "echo \"Worker ${i} — watching ${WORKTREE}\"; echo; while true; do if [ -d '${WORKTREE}' ]; then echo '--- signals.py diff ---'; cd '${WORKTREE}' && git diff --stat signals.py 2>/dev/null; echo; echo '--- Last commit ---'; git log --oneline -3 2>/dev/null; else echo 'Worktree not created yet'; fi; sleep 10; done"
done

# Arrange panes in a tiled layout
tmux select-layout -t "$SESSION" tiled

# Set pane titles
tmux select-pane -t "$SESSION:0.0" -T "Leader Status"
for i in $(seq 0 $((NUM_WORKERS - 1))); do
    tmux select-pane -t "$SESSION:0.$((i + 1))" -T "Worker ${i}"
done

# Enable pane borders with titles
tmux set-option -t "$SESSION" pane-border-status top
tmux set-option -t "$SESSION" pane-border-format " #{pane_title} "

echo "Dashboard started. Attach with: tmux attach -t $SESSION"
echo "Kill with: ./swarm-board.sh --kill"

# Attach if running interactively
if [ -t 0 ]; then
    tmux attach -t "$SESSION"
fi
