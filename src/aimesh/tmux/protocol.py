"""Outbox protocol for AI Mesh tmux workers.

Workers write JSON response files to an outbox directory.
The orchestrator polls that directory to collect responses.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class OutboxResponse:
    id: str
    reply_to: str
    type: str  # "chat_response" | "task_decomposition" | "classification"
    content: str
    structured_data: dict
    timestamp: str


def write_response(outbox_dir: str | Path, response_data: dict) -> Path:
    """Write a JSON response file atomically to outbox_dir.

    Generates a UUID-based filename, writes to a .tmp file, then renames
    to the final .json path (atomic on POSIX).

    Args:
        outbox_dir: Directory where response files are written.
        response_data: Dict to serialise as JSON.

    Returns:
        Path of the written .json file.
    """
    outbox_dir = Path(outbox_dir)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())
    tmp_path = outbox_dir / f"{file_id}.json.tmp"
    final_path = outbox_dir / f"{file_id}.json"

    tmp_path.write_text(json.dumps(response_data), encoding="utf-8")
    tmp_path.rename(final_path)

    return final_path


def poll_outbox(outbox_dir: str | Path) -> list[dict]:
    """Read all .json files from outbox_dir and move them to processed/.

    Malformed JSON files are logged and moved to processed/ without being
    included in the returned list.

    Args:
        outbox_dir: Directory to poll for response files.

    Returns:
        List of successfully parsed response dicts.
    """
    outbox_dir = Path(outbox_dir)
    if not outbox_dir.exists():
        return []

    processed_dir = outbox_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for json_file in sorted(outbox_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            results.append(data)
        except json.JSONDecodeError:
            logger.warning(
                "malformed_json_in_outbox",
                file=str(json_file),
            )
        finally:
            dest = processed_dir / json_file.name
            json_file.rename(dest)

    return results


def build_outbox_instructions(outbox_path: str) -> str:
    """Return a markdown string for inclusion in CLAUDE.md.

    Describes the response protocol workers must follow when writing
    files to the outbox directory.

    Args:
        outbox_path: Absolute path to the outbox directory shown to workers.

    Returns:
        Markdown-formatted instruction string.
    """
    return f"""\
## Response Protocol
When you need to respond, write a JSON file to {outbox_path} using the Write tool.
File name: use a unique name like response-001.json

Required JSON format:
{{
  "id": "unique-response-id",
  "reply_to": "the-message-id-from-the-prompt",
  "type": "chat_response",
  "content": "your response text",
  "structured_data": {{}},
  "timestamp": "ISO8601"
}}"""
