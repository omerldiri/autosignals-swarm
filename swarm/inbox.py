"""
Filesystem-based inbox for inter-agent coordination.

ClawTeam-inspired pattern: no external dependencies, just JSON files on disk.

Directory structure:
    .swarm/inbox/<agent_name>/
        <timestamp>_<sender>.json

Each message:
    {
        "id": "<uuid>",
        "sender": "<agent_name>",
        "recipient": "<agent_name>",
        "type": "result|task|crosspolinate|status|kill",
        "content": { ... },
        "timestamp": "<iso8601>"
    }
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional


SWARM_DIR = Path(".swarm")
INBOX_DIR = SWARM_DIR / "inbox"


def _ensure_inbox(agent_name: str) -> Path:
    """Create inbox directory for an agent if it doesn't exist."""
    inbox = INBOX_DIR / agent_name
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def send(sender: str, recipient: str, msg_type: str, content: dict) -> str:
    """
    Send a message to an agent's inbox.

    Returns the message ID.
    """
    msg_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().isoformat()

    message = {
        "id": msg_id,
        "sender": sender,
        "recipient": recipient,
        "type": msg_type,
        "content": content,
        "timestamp": timestamp,
    }

    inbox = _ensure_inbox(recipient)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sender}_{msg_id}.json"
    filepath = inbox / filename

    with open(filepath, "w") as f:
        json.dump(message, f, indent=2)

    return msg_id


def receive(agent_name: str, msg_type: Optional[str] = None) -> List[dict]:
    """
    Read and consume all messages from an agent's inbox.

    Optionally filter by message type. Messages are deleted after reading.
    Returns list of messages sorted by timestamp (oldest first).
    """
    inbox = _ensure_inbox(agent_name)
    messages = []

    for filepath in sorted(inbox.glob("*.json")):
        try:
            with open(filepath) as f:
                msg = json.load(f)
            if msg_type is None or msg.get("type") == msg_type:
                messages.append(msg)
                filepath.unlink()  # consume the message
        except (json.JSONDecodeError, OSError):
            continue

    return messages


def peek(agent_name: str, msg_type: Optional[str] = None) -> List[dict]:
    """
    Read messages without consuming them.
    """
    inbox = _ensure_inbox(agent_name)
    messages = []

    for filepath in sorted(inbox.glob("*.json")):
        try:
            with open(filepath) as f:
                msg = json.load(f)
            if msg_type is None or msg.get("type") == msg_type:
                messages.append(msg)
        except (json.JSONDecodeError, OSError):
            continue

    return messages


def broadcast(sender: str, recipients: List[str], msg_type: str, content: dict) -> List[str]:
    """
    Send the same message to multiple agents.

    Returns list of message IDs.
    """
    return [send(sender, r, msg_type, content) for r in recipients]


def count(agent_name: str, msg_type: Optional[str] = None) -> int:
    """Count pending messages in an agent's inbox."""
    inbox = _ensure_inbox(agent_name)
    if not inbox.exists():
        return 0

    total = 0
    for filepath in inbox.glob("*.json"):
        if msg_type is None:
            total += 1
        else:
            try:
                with open(filepath) as f:
                    msg = json.load(f)
                if msg.get("type") == msg_type:
                    total += 1
            except (json.JSONDecodeError, OSError):
                continue
    return total


def clear_inbox(agent_name: str):
    """Delete all messages in an agent's inbox."""
    inbox = _ensure_inbox(agent_name)
    for filepath in inbox.glob("*.json"):
        filepath.unlink(missing_ok=True)
